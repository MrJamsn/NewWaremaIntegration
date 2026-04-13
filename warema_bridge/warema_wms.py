"""
warema_wms.py - Python reimplementation of warema-wms-venetian-blinds npm package

Protocol reverse-engineered from the npm package source (MIT licensed).
Original credits: "Pman" and "willjoha" on ioBroker forum.

Requirements:
    pip install pyserial-asyncio

Usage:
    import asyncio
    from warema_wms import WmsStick

    async def main():
        stick = WmsStick(
            port="/dev/ttyUSB0",
            channel=17,
            pan_id="1A2B",
            key="0123456789ABCDEF0123456789ABCDEF",
        )
        await stick.connect()
        await stick.init_network()

        # Scan for devices
        devices = await stick.scan_devices()
        print(devices)

        # Add a blind by serial number
        stick.add_blind(580911)

        # Get position
        pos = await stick.get_position(580911)
        print(pos)  # {"position": 50, "angle": 0, "moving": False}

        # Move to position (0=open, 100=closed; angle -100..100)
        await stick.set_position(580911, position=50, angle=0)

        # Stop
        await stick.stop(580911)

        await stick.disconnect()

    asyncio.run(main())
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

import serial_asyncio

log = logging.getLogger(__name__)

BAUD_RATE = 125000
DELIMITER = b"}"
WMS_ANGLE_RANGE = 75  # degrees from center


# ---------------------------------------------------------------------------
# SNR encoding helpers
# The WMS protocol uses a 3-byte little-endian serial number in hex.
# e.g. decimal 580911 = 0x0A2469 → bytes reversed → "69240A"
# ---------------------------------------------------------------------------

def snr_int_to_hex(snr: int) -> str:
    """Convert integer SNR to 6-char WMS hex string (little-endian byte order)."""
    h = f"{snr:06X}"           # e.g. "0A2469"
    return h[4:6] + h[2:4] + h[0:2]   # swap bytes → "69240A"


def snr_hex_to_int(hex_str: str) -> int:
    """Convert 6-char WMS hex string back to integer SNR."""
    h = hex_str.upper().zfill(6)
    swapped = h[4:6] + h[2:4] + h[0:2]
    return int(swapped, 16)


# ---------------------------------------------------------------------------
# Position / angle encoding
# ---------------------------------------------------------------------------

def pos_hex_to_pct(hex2: str) -> int:
    """Convert 2-char hex position byte to 0-100 percent."""
    return min(100, max(0, round(int(hex2, 16) / 2)))


def pos_pct_to_hex(pct: int) -> str:
    """Convert 0-100 percent to 2-char hex position byte."""
    val = min(max(pct, 0), 100) * 2
    return f"{val:02X}"


def angle_hex_to_pct(hex2: str) -> int:
    """Convert 2-char hex angle byte to -100..+100 percent."""
    return round((int(hex2, 16) - 127) / WMS_ANGLE_RANGE * 100)


def angle_pct_to_hex(pct: int) -> str:
    """Convert -100..+100 percent to 2-char hex angle byte."""
    val = min(max(round(pct / 100 * WMS_ANGLE_RANGE), -75), 75) + 127
    return f"{val:02X}"


# ---------------------------------------------------------------------------
# Protocol encode / decode
# ---------------------------------------------------------------------------

def encode_cmd(cmd: str, snr_hex: str, params: dict) -> dict:
    """
    Build a raw WMS command string and the expected response type.

    Returns {"cmd": str, "expect_type": str, "expect_snr": str|None}
    """
    r = {"cmd": "", "expect_type": "", "expect_snr": None}

    if cmd == "blind_get_pos":
        r["expect_type"] = "position"
        r["expect_snr"] = snr_hex
        r["cmd"] = "{R06" + snr_hex + "801001000005}"

    elif cmd == "blind_move_to_pos":
        pos = params.get("pos", 0)
        ang = params.get("ang", 0)
        r["expect_type"] = "blind_move_to_pos_response"
        r["expect_snr"] = snr_hex
        r["cmd"] = "{R06" + snr_hex + "7070" + "03" + pos_pct_to_hex(pos) + angle_pct_to_hex(ang) + "FFFF}"

    elif cmd == "blind_stop_move":
        r["cmd"] = "{R06" + snr_hex + "707001FFFFFFFF00}"
        r["expect_type"] = "blind_move_to_pos_response"
        r["expect_snr"] = snr_hex

    elif cmd == "stick_get_name":
        r["cmd"] = "{G}"
        r["expect_type"] = "stick_name"

    elif cmd == "stick_get_version":
        r["cmd"] = "{V}"
        r["expect_type"] = "stick_version"

    elif cmd == "stick_set_key":
        r["cmd"] = "{K401" + params["key"] + "}"
        r["expect_type"] = "ack"

    elif cmd == "stick_switch_channel":
        channel = params["channel"]
        pan_id = params["pan_id"]
        r["cmd"] = "{M%" + f"{channel:02d}" + pan_id + "}"
        r["expect_type"] = "ack"

    elif cmd == "scan_request":
        r["cmd"] = "{R04FFFFFF7020" + params["pan_id"] + "02}"
        r["expect_type"] = ""  # No direct response; scan replies come unsolicited

    elif cmd == "scan_response":
        r["cmd"] = "{R01" + snr_hex + "7021" + params["pan_id"] + "02}"
        r["expect_type"] = "ack_msg"
        r["expect_snr"] = snr_hex

    elif cmd == "ack_msg":
        r["cmd"] = "{R21" + snr_hex + "50AC}"
        r["expect_type"] = "ack"

    elif cmd == "ack":
        r["cmd"] = "{a}"
        r["expect_type"] = ""

    elif cmd in ("wave_request", "blind_beckon_request"):
        r["cmd"] = "{R06" + snr_hex + "7050}"
        r["expect_type"] = "ack_msg"
        r["expect_snr"] = snr_hex

    else:
        log.error("encode_cmd: unknown command '%s'", cmd)

    return r


def decode_frame(raw: str) -> dict:
    """
    Parse a raw WMS frame string (including trailing '}') into a message dict.

    Returns {"msg_type": str, "snr": str, "snr_int": int, "params": dict}
    """
    msg_type = "unknown"
    snr = "000000"
    params = {"raw": raw}

    if raw.startswith("{a}"):
        msg_type = "ack"

    elif raw.startswith("{f}"):
        msg_type = "fwd"

    elif raw.startswith("{g"):
        msg_type = "stick_name"
        params["name"] = raw[2:].rstrip("}")

    elif raw.startswith("{v"):
        msg_type = "stick_version"
        params["version"] = raw[2:].rstrip("}")

    elif raw.startswith("{r"):
        # {r<SNR(6)><TYPE(4)><PAYLOAD...>}
        snr = raw[2:8]
        rcv_type = raw[8:12]
        payload = raw[12:].rstrip("}")

        if rcv_type == "8011":
            # Parameter get response
            param_type = payload[0:8]
            if param_type in ("01000003", "01000005"):
                msg_type = "position"
                params["position"] = pos_hex_to_pct(payload[8:10])
                params["angle"] = angle_hex_to_pct(payload[10:12])
                params["valance_1"] = payload[12:14]
                params["valance_2"] = payload[14:16]
                params["moving"] = payload[16:18] != "00"
            elif param_type == "0C000006":
                msg_type = "auto_settings"
                params["wind"] = int(payload[12:14], 16)
                params["rain"] = int(payload[22:24], 16)
                params["sun"] = int(payload[24:26], 16)
                params["dusk"] = int(payload[26:28], 16)
            else:
                msg_type = "parameter_get_response"

        elif rcv_type == "7071":
            msg_type = "blind_move_to_pos_response"
            params["prev_position"] = pos_hex_to_pct(payload[10:12])
            params["prev_angle"] = angle_hex_to_pct(payload[12:14])

        elif rcv_type == "7080":
            msg_type = "weather_broadcast"
            wind_raw = int(payload[2:4], 16)
            lumen_hi = int(payload[4:6], 16)
            lumen_lo = int(payload[12:14], 16)
            params["wind"] = wind_raw
            params["lumen"] = lumen_lo * 2 if lumen_hi == 0 else lumen_hi * lumen_lo * 2
            params["rain"] = payload[16:18] == "C8"
            params["temp"] = int(payload[18:20], 16) / 2 - 35

        elif rcv_type == "8020":
            param_type = payload[0:8]
            if param_type == "0B080009":
                msg_type = "clock"
                params["year"] = int(payload[8:10], 16)
                params["month"] = int(payload[10:12], 16)
                params["day"] = int(payload[12:14], 16)
                params["hour"] = int(payload[14:16], 16)
                params["minute"] = int(payload[16:18], 16)
                params["second"] = int(payload[18:20], 16)
                params["day_of_week"] = int(payload[20:22], 16)

        elif rcv_type == "5018":
            msg_type = "join_network_request"
            params["pan_id"] = payload[0:4]
            # Key bytes are reversed
            key_bytes = payload[4:36]
            params["network_key"] = "".join(
                reversed([key_bytes[i:i+2] for i in range(0, 32, 2)])
            )
            params["channel"] = int(payload[38:40], 16)

        elif rcv_type == "5060":
            msg_type = "switch_channel_request"
            params["pan_id"] = payload[0:4]
            params["device_type"] = payload[4:6]
            params["channel"] = int(payload[6:8], 16)

        elif rcv_type == "50AC":
            msg_type = "ack_msg"

        elif rcv_type == "7020":
            msg_type = "scan_request"
            params["pan_id"] = payload[0:4]
            params["device_type"] = payload[4:6]

        elif rcv_type == "7021":
            msg_type = "scan_response"
            params["pan_id"] = payload[0:4]
            params["device_type"] = payload[4:6]
            DEVICE_TYPES = {
                "02": "Stick/software",
                "06": "Weather station",
                "07": "Remote control (+)",
                "20": "Actuator UP",
                "21": "Plug receiver",
                "25": "Radio motor",
                "2E": "Actuator 230V UP",
                "63": "Web control",
            }
            params["device_type_str"] = DEVICE_TYPES.get(params["device_type"], "<unknown>")

        elif rcv_type == "7050":
            msg_type = "wave_request"

        elif rcv_type == "7070":
            msg_type = "blind_move_to_pos"
            params["position"] = pos_hex_to_pct(payload[2:4])
            params["angle"] = angle_hex_to_pct(payload[4:6])

        elif rcv_type == "8010":
            msg_type = "parameter_get_request"
            params["parameter"] = payload

    return {
        "msg_type": msg_type,
        "snr": snr,
        "snr_int": snr_hex_to_int(snr),
        "params": params,
    }


# ---------------------------------------------------------------------------
# Queued command
# ---------------------------------------------------------------------------

@dataclass
class _QueuedCmd:
    cmd: str                        # logical command name
    snr_hex: str                    # "000000" for stick commands
    params: dict
    encoded: dict = field(default_factory=dict)
    timeout_ms: int = 2000
    delay_after_ms: int = 0
    retries: int = -1               # -1 = no retry
    future: Optional[asyncio.Future] = None

    def __post_init__(self):
        self.encoded = encode_cmd(self.cmd, self.snr_hex, self.params)
        timeouts = {
            "blind_get_pos":      (500,  100, 5),
            "blind_move_to_pos":  (500,  300, 3),
            "blind_stop_move":    (200,    5, 3),
            "wave_request":       (500,  300, -1),
            "scan_request":       (750,    0, -1),
        }
        if self.cmd in timeouts:
            self.timeout_ms, self.delay_after_ms, self.retries = timeouts[self.cmd]


# ---------------------------------------------------------------------------
# Blind state
# ---------------------------------------------------------------------------

@dataclass
class BlindState:
    snr: int
    snr_hex: str
    name: str
    position: int = -1   # -1 = unknown
    angle: int = 0
    moving: bool = False
    added_at: datetime = field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# Main WmsStick class
# ---------------------------------------------------------------------------

class WmsStick:
    """
    Async Python interface to a Warema WMS USB Stick.

    All public methods are coroutines (async def).
    """

    MOTORIZED_DEVICE_TYPES = {"20", "21", "25", "2E"}

    def __init__(
        self,
        port: str,
        channel: int,
        pan_id: str,
        key: str,
        on_weather: Optional[Callable] = None,
        on_position: Optional[Callable] = None,
    ):
        self.port = port
        self.channel = channel
        self.pan_id = pan_id.upper()
        self.key = key.upper()
        self.on_weather = on_weather      # callback(weather_dict)
        self.on_position = on_position    # callback(blind_state)

        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._read_task: Optional[asyncio.Task] = None

        self._queue: asyncio.Queue = asyncio.Queue()
        self._current: Optional[_QueuedCmd] = None
        self._queue_task: Optional[asyncio.Task] = None

        self._blinds: dict[str, BlindState] = {}   # snr_hex → BlindState
        self._scanned: dict[str, dict] = {}         # snr_hex → device info

        self.weather: dict = {}
        self.status: str = "disconnected"

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    async def connect(self):
        """Open the serial port."""
        self._reader, self._writer = await serial_asyncio.open_serial_connection(
            url=self.port, baudrate=BAUD_RATE
        )
        self.status = "connected"
        log.info("Serial port %s opened at %d baud", self.port, BAUD_RATE)
        self._read_task = asyncio.create_task(self._read_loop())
        self._queue_task = asyncio.create_task(self._queue_loop())

    async def disconnect(self):
        """Close the serial port and cancel background tasks."""
        if self._read_task:
            self._read_task.cancel()
        if self._queue_task:
            self._queue_task.cancel()
        if self._writer:
            self._writer.close()
        self.status = "disconnected"
        log.info("Disconnected from %s", self.port)

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    async def init_network(self):
        """
        Send the stick initialisation sequence:
        get name → get version → set key → switch channel.
        Raises asyncio.TimeoutError if the stick doesn't respond.
        """
        await self._enqueue("stick_get_name",    "000000", {})
        await self._enqueue("stick_get_version", "000000", {})
        await self._enqueue("stick_set_key",     "000000", {"key": self.key})
        await self._enqueue(
            "stick_switch_channel", "000000",
            {"channel": self.channel, "pan_id": self.pan_id}
        )
        self.status = "ready"
        log.info("WMS network initialised (channel=%d pan_id=%s)", self.channel, self.pan_id)

    # ------------------------------------------------------------------
    # Network parameter discovery (panId = "FFFF")
    # ------------------------------------------------------------------

    async def discover_network_params(self, timeout_s: int = 180) -> dict:
        """
        Enter discovery mode (use pan_id="FFFF" in constructor).
        Waits for a remote to teach in the stick and returns the network params.

        Returns {"channel": int, "pan_id": str, "network_key": str}
        """
        log.info("--- Discovery mode ---")
        log.info("Open the battery cover of the WMS Handheld transmitter.")
        log.info("Select channel, press learn button ~5s, then press STOP when stick is found.")

        result_future: asyncio.Future = asyncio.get_event_loop().create_future()
        original_handler = self._on_unsolicited

        def _capture(msg):
            if msg["msg_type"] == "join_network_request" and not result_future.done():
                result_future.set_result(msg["params"])
            else:
                original_handler(msg)

        self._on_unsolicited = _capture  # type: ignore[method-assign]
        await self.init_network()
        try:
            return await asyncio.wait_for(result_future, timeout=timeout_s)
        except asyncio.TimeoutError:
            log.error("Discovery timed out after %ds", timeout_s)
            raise
        finally:
            self._on_unsolicited = original_handler  # type: ignore[method-assign]

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    async def scan_devices(self) -> list[dict]:
        """
        Broadcast scan requests and collect responses for ~3 seconds.
        Returns list of {"snr": int, "snr_hex": str, "device_type": str, "device_type_str": str}.
        """
        self._scanned.clear()
        log.info("Scanning for WMS devices...")
        # Send 3 scan requests (some devices miss the first one)
        for _ in range(3):
            await self._enqueue("scan_request", "000000", {"pan_id": self.pan_id})
        await asyncio.sleep(2.5)  # Allow time for scan responses to arrive
        devices = list(self._scanned.values())
        log.info("Found %d devices", len(devices))
        return devices

    # ------------------------------------------------------------------
    # Blind management
    # ------------------------------------------------------------------

    def add_blind(self, snr: int, name: Optional[str] = None) -> BlindState:
        """Register a blind by its integer serial number."""
        snr_hex = snr_int_to_hex(snr)
        if name is None:
            name = f"Blind {snr} ({snr_hex})"
        blind = BlindState(snr=snr, snr_hex=snr_hex, name=name)
        self._blinds[snr_hex] = blind
        log.info("Added blind: %s", name)
        return blind

    def remove_blind(self, snr: int):
        snr_hex = snr_int_to_hex(snr)
        self._blinds.pop(snr_hex, None)

    def list_blinds(self) -> list[BlindState]:
        return list(self._blinds.values())

    def get_blind_state(self, snr: int) -> Optional[BlindState]:
        return self._blinds.get(snr_int_to_hex(snr))

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def get_position(self, snr: int) -> dict:
        """
        Request the current position of a blind.
        Returns {"position": int, "angle": int, "moving": bool}.
        """
        snr_hex = snr_int_to_hex(snr)
        msg = await self._enqueue("blind_get_pos", snr_hex, {})
        return {
            "position": msg["params"]["position"],
            "angle": msg["params"]["angle"],
            "moving": msg["params"]["moving"],
        }

    async def set_position(self, snr: int, position: int, angle: int = 0):
        """
        Move a blind to the specified position (0=open, 100=closed)
        and optional angle (-100..+100).
        """
        snr_hex = snr_int_to_hex(snr)
        await self._enqueue("blind_move_to_pos", snr_hex, {"pos": position, "ang": angle})
        blind = self._blinds.get(snr_hex)
        if blind:
            blind.moving = True

    async def stop(self, snr: int):
        """Stop a moving blind."""
        snr_hex = snr_int_to_hex(snr)
        await self._enqueue("blind_stop_move", snr_hex, {})

    async def wave(self, snr: int):
        """Send a wave/beckon request to visually identify a blind."""
        snr_hex = snr_int_to_hex(snr)
        await self._enqueue("wave_request", snr_hex, {})

    async def tilt_up(self, snr: int):
        """Tilt slats one step up."""
        await self._tilt(snr, -1)

    async def tilt_down(self, snr: int):
        """Tilt slats one step down."""
        await self._tilt(snr, 1)

    async def _tilt(self, snr: int, direction: int):
        pos_info = await self.get_position(snr)
        current_angle = pos_info["angle"]
        step = 100 / 3
        new_angle = round((round(current_angle / step) + direction) * step)
        new_angle = max(-100, min(100, new_angle))
        await self.set_position(snr, pos_info["position"], new_angle)

    # ------------------------------------------------------------------
    # Internal: command queue
    # ------------------------------------------------------------------

    async def _enqueue(self, cmd: str, snr_hex: str, params: dict) -> dict:
        """Enqueue a command and wait for its response."""
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        queued = _QueuedCmd(cmd=cmd, snr_hex=snr_hex, params=params, future=future)
        await self._queue.put(queued)
        return await future

    async def _queue_loop(self):
        """Process commands one at a time from the queue."""
        while True:
            queued: _QueuedCmd = await self._queue.get()
            await self._execute(queued)

    async def _execute(self, queued: _QueuedCmd):
        """Send a command and wait for the expected response with retries."""
        retries_left = queued.retries

        while True:
            raw_cmd = queued.encoded["cmd"]
            log.debug("SND: %s", raw_cmd)
            self._writer.write(raw_cmd.encode())

            expect_type = queued.encoded.get("expect_type", "")
            expect_snr = queued.encoded.get("expect_snr")

            if not expect_type:
                # Fire and forget
                if queued.future and not queued.future.done():
                    queued.future.set_result({"msg_type": "none", "params": {}})
                await asyncio.sleep(queued.delay_after_ms / 1000)
                return

            # Wait for the expected response
            response_future: asyncio.Future = asyncio.get_event_loop().create_future()
            self._current = queued
            self._pending_response = response_future
            self._expect_type = expect_type
            self._expect_snr = expect_snr

            try:
                msg = await asyncio.wait_for(
                    response_future, timeout=queued.timeout_ms / 1000
                )
                if queued.future and not queued.future.done():
                    queued.future.set_result(msg)
                if queued.delay_after_ms:
                    await asyncio.sleep(queued.delay_after_ms / 1000)
                return

            except asyncio.TimeoutError:
                if retries_left > 0:
                    retries_left -= 1
                    log.debug("Timeout, retrying %s (retries left: %d)", queued.cmd, retries_left)
                    continue
                else:
                    log.warning("Timeout with no retries left for %s %s", queued.cmd, queued.snr_hex)
                    if queued.future and not queued.future.done():
                        queued.future.set_exception(asyncio.TimeoutError())
                    return
            finally:
                self._current = None
                self._pending_response = None
                self._expect_type = None
                self._expect_snr = None

    # ------------------------------------------------------------------
    # Internal: serial reading
    # ------------------------------------------------------------------

    async def _read_loop(self):
        """Continuously read frames from the serial port."""
        buf = b""
        while True:
            try:
                chunk = await self._reader.read(256)
                buf += chunk
                while DELIMITER in buf:
                    frame_bytes, buf = buf.split(DELIMITER, 1)
                    frame = frame_bytes.decode("utf-8", errors="ignore") + "}"
                    frame = frame.strip()
                    if frame:
                        log.debug("RCV: %s", frame)
                        self._handle_frame(frame)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("Read error: %s", e)
                await asyncio.sleep(0.1)

    def _handle_frame(self, raw: str):
        """Decode a received frame and dispatch it."""
        msg = decode_frame(raw)
        msg_type = msg["msg_type"]
        snr = msg["snr"]

        # Check if this is the response we're waiting for
        if (
            self._pending_response is not None
            and not self._pending_response.done()
            and self._expect_type == msg_type
            and (self._expect_snr is None or self._expect_snr == snr)
        ):
            self._pending_response.set_result(msg)
            return

        # Unsolicited messages
        self._on_unsolicited(msg)

    def _on_unsolicited(self, msg: dict):
        msg_type = msg["msg_type"]
        snr = msg["snr"]
        params = msg["params"]

        if msg_type == "weather_broadcast":
            self.weather = {
                "snr": msg["snr_int"],
                "snr_hex": snr,
                "timestamp": datetime.now().isoformat(),
                "temp_c": params.get("temp"),
                "wind": params.get("wind"),
                "lumen": params.get("lumen"),
                "rain": params.get("rain"),
            }
            log.debug("Weather: %s", self.weather)
            if self.on_weather:
                self.on_weather(self.weather)

        elif msg_type == "scan_response":
            self._scanned[snr] = {
                "snr": msg["snr_int"],
                "snr_hex": snr,
                "device_type": params.get("device_type"),
                "device_type_str": params.get("device_type_str", "<unknown>"),
            }
            log.info(
                "Scanned: %s (%s) - %s",
                snr, msg["snr_int"], params.get("device_type_str")
            )

        elif msg_type == "position":
            blind = self._blinds.get(snr)
            if blind:
                blind.position = params.get("position", blind.position)
                blind.angle = params.get("angle", blind.angle)
                blind.moving = params.get("moving", False)
                log.debug("Position update %s: pos=%d ang=%d moving=%s",
                          blind.name, blind.position, blind.angle, blind.moving)
                if self.on_position:
                    self.on_position(blind)

        elif msg_type == "scan_request":
            # Stick is being scanned by a remote — send scan_response
            asyncio.create_task(
                self._enqueue("scan_response", snr, {"pan_id": self.pan_id})
            )

        elif msg_type == "join_network_request":
            log.info(
                "Network params received: channel=%d pan_id=%s key=%s",
                params.get("channel"), params.get("pan_id"), params.get("network_key")
            )

        elif msg_type == "switch_channel_request":
            asyncio.create_task(
                self._enqueue("stick_switch_channel", "000000", {
                    "channel": params.get("channel"),
                    "pan_id": params.get("pan_id"),
                })
            )

        elif msg_type not in ("ack", "fwd", "unknown"):
            log.debug("Unhandled message: %s from %s", msg_type, snr)


# ---------------------------------------------------------------------------
# Convenience: list serial ports containing a WMS Stick
# ---------------------------------------------------------------------------

async def find_wms_sticks() -> list[str]:
    """
    Probe all serial ports at 125000 baud and return those that respond to {V}.
    """
    import serial.tools.list_ports
    candidates = [p.device for p in serial.tools.list_ports.comports()]
    found = []

    for port_path in candidates:
        try:
            reader, writer = await serial_asyncio.open_serial_connection(
                url=port_path, baudrate=BAUD_RATE
            )
            writer.write(b"{V}")
            try:
                data = await asyncio.wait_for(reader.read(64), timeout=1.0)
                if data and data.startswith(b"{v"):
                    log.info("WMS Stick found at %s", port_path)
                    found.append(port_path)
            except asyncio.TimeoutError:
                pass
            finally:
                writer.close()
        except Exception:
            pass

    return found


# ---------------------------------------------------------------------------
# CLI demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Warema WMS CLI")
    parser.add_argument("--port", default="/dev/ttyUSB0")
    parser.add_argument("--channel", type=int, default=17)
    parser.add_argument("--pan-id", default="FFFF",
                        help="Use FFFF to discover network parameters")
    parser.add_argument("--key", default="00112233445566778899AABBCCDDEEFF")
    parser.add_argument("--scan", action="store_true", help="Scan for devices")
    parser.add_argument("--snr", type=int, help="Blind SNR for commands")
    parser.add_argument("--pos", type=int, help="Set position (0-100)")
    parser.add_argument("--angle", type=int, default=0, help="Set angle (-100 to 100)")
    parser.add_argument("--stop", action="store_true", help="Stop blind")
    parser.add_argument("--wave", action="store_true", help="Wave/beckon blind")
    args = parser.parse_args()

    async def run():
        stick = WmsStick(
            port=args.port,
            channel=args.channel,
            pan_id=args.pan_id,
            key=args.key,
            on_position=lambda b: print(
                f"Position update: {b.name} pos={b.position} angle={b.angle} moving={b.moving}"
            ),
            on_weather=lambda w: print(f"Weather: {w}"),
        )

        await stick.connect()

        if args.pan_id.upper() == "FFFF":
            print("Discovery mode — follow the instructions on your WMS remote.")
            params = await stick.discover_network_params()
            print(f"\nNetwork parameters found:")
            print(f"  Channel:     {params['channel']}")
            print(f"  PAN ID:      {params['pan_id']}")
            print(f"  Network Key: {params['network_key']}")
            await stick.disconnect()
            return

        await stick.init_network()

        if args.scan:
            devices = await stick.scan_devices()
            print("\nDevices found:")
            for d in devices:
                print(f"  SNR {d['snr']} ({d['snr_hex']}) - {d['device_type_str']}")

        if args.snr:
            stick.add_blind(args.snr)

            if args.pos is not None:
                print(f"Moving SNR {args.snr} to position {args.pos}, angle {args.angle}")
                await stick.set_position(args.snr, args.pos, args.angle)
                await asyncio.sleep(1)

            if args.stop:
                print(f"Stopping SNR {args.snr}")
                await stick.stop(args.snr)

            if args.wave:
                print(f"Waving SNR {args.snr}")
                await stick.wave(args.snr)

            if args.pos is None and not args.stop and not args.wave:
                pos = await stick.get_position(args.snr)
                print(f"SNR {args.snr}: position={pos['position']} angle={pos['angle']} moving={pos['moving']}")

        await asyncio.sleep(0.5)
        await stick.disconnect()

    asyncio.run(run())
