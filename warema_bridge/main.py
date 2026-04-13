"""
main.py - Warema WMS to Home Assistant MQTT bridge

Reads config from environment variables (set by run.sh from HA addon options).
Uses MQTT autodiscovery so blinds appear automatically in Home Assistant as covers.

MQTT topics (per blind, SNR as identifier):
  Discovery:        homeassistant/cover/warema_<snr>/config
  Position state:   warema/<snr>/position_state    (0-100, 0=open)
  Availability:     warema/<snr>/availability       (online/offline)
  Commands:
    warema/<snr>/set           OPEN | CLOSE | STOP
    warema/<snr>/set_position  0-100 (0=open, 100=closed)
"""

import asyncio
import json
import logging
import os
import signal
from typing import Optional

import aiomqtt

from warema_wms import WmsStick, snr_int_to_hex

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------

def get_env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()

def get_env_int(key: str, default: int) -> int:
    try:
        return int(get_env(key, str(default)))
    except ValueError:
        return default

MQTT_SERVER       = (get_env("MQTT_SERVER") or "core-mosquitto").removeprefix("mqtt://").removeprefix("mqtts://")
MQTT_PORT         = get_env_int("MQTT_PORT", 1883)
MQTT_USER         = get_env("MQTT_USER") or None
MQTT_PASSWORD     = get_env("MQTT_PASSWORD") or None

WMS_PORT          = get_env("WMS_SERIAL_PORT", "/dev/ttyUSB0")
WMS_CHANNEL       = get_env_int("WMS_CHANNEL", 17)
WMS_PAN_ID        = get_env("WMS_PAN_ID", "FFFF")
WMS_KEY           = get_env("WMS_KEY", "00112233445566778899AABBCCDDEEFF")
POLLING_INTERVAL  = get_env_int("POLLING_INTERVAL", 30)    # seconds
MOVING_INTERVAL   = get_env_int("MOVING_INTERVAL", 2)      # seconds

IGNORED_DEVICES   = {s.strip() for s in get_env("IGNORED_DEVICES").split(",") if s.strip()}
FORCE_DEVICES     = {}  # snr_hex -> device_type (parsed below)

for entry in get_env("FORCE_DEVICES").split(","):
    entry = entry.strip()
    if not entry:
        continue
    if ":" in entry:
        snr_s, dtype = entry.split(":", 1)
        FORCE_DEVICES[snr_s.strip()] = dtype.strip()
    elif entry:
        FORCE_DEVICES[entry] = "25"  # default: radio motor

LOG_LEVEL = get_env("LOG_LEVEL", "info").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("warema_bridge")

# ---------------------------------------------------------------------------
# MQTT topic helpers
# ---------------------------------------------------------------------------

DISCOVERY_PREFIX = "homeassistant"
STATE_PREFIX = "warema"


def topic_discovery(snr: int) -> str:
    return f"{DISCOVERY_PREFIX}/cover/warema_{snr}/config"

def topic_position(snr: int) -> str:
    return f"{STATE_PREFIX}/{snr}/position_state"

def topic_availability(snr: int) -> str:
    return f"{STATE_PREFIX}/{snr}/availability"

def topic_cmd_set(snr: int) -> str:
    return f"{STATE_PREFIX}/{snr}/set"

def topic_cmd_position(snr: int) -> str:
    return f"{STATE_PREFIX}/{snr}/set_position"

def topic_tilt(snr: int) -> str:
    return f"{STATE_PREFIX}/{snr}/tilt"

def topic_tilt_state(snr: int) -> str:
    return f"{STATE_PREFIX}/{snr}/tilt_state"


def discovery_payload(snr: int, name: str) -> dict:
    """Build the HA MQTT discovery payload for a cover entity."""
    uid = f"warema_{snr}"
    return {
        "name": name,
        "unique_id": uid,
        "device": {
            "identifiers": [uid],
            "name": name,
            "manufacturer": "Warema",
            "model": "WMS Motor",
        },
        "availability_topic": topic_availability(snr),
        "payload_available": "online",
        "payload_not_available": "offline",
        # Position: HA uses 0=closed, 100=open — inverted from WMS (0=open, 100=closed)
        "position_topic": topic_position(snr),
        "position_open": 100,
        "position_closed": 0,
        "set_position_topic": topic_cmd_position(snr),
        "set_position_template": "{{ 100 - position | int }}",   # invert for WMS
        "command_topic": topic_cmd_set(snr),
        "payload_open": "OPEN",
        "payload_close": "CLOSE",
        "payload_stop": "STOP",
        # Tilt / slat angle: HA 0-100, centre (50) = slats horizontal (max light)
        "tilt_command_topic": topic_tilt(snr),
        "tilt_status_topic": topic_tilt_state(snr),
        "tilt_min": 0,
        "tilt_max": 100,
        "tilt_opened_value": 50,   # horizontal = open for light
        "tilt_closed_value": 0,    # fully tilted = blocking light
        "optimistic": False,
    }


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------

class WaremaBridge:
    def __init__(self):
        self.stick: Optional[WmsStick] = None
        self.mqtt: Optional[aiomqtt.Client] = None
        self._registered_snrs: set[int] = set()
        self._poll_task: Optional[asyncio.Task] = None
        self._moving_snrs: set[int] = set()
        self._move_poll_task: Optional[asyncio.Task] = None
        self._moving_event: asyncio.Event = asyncio.Event()

    def _start_tracking(self, snr: int):
        """Add a blind to the fast-poll set and wake the moving-poll loop immediately."""
        self._moving_snrs.add(snr)
        self._moving_event.set()

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    async def run(self):
        log.info("Starting Warema WMS bridge")
        log.info("  Port:     %s", WMS_PORT)
        log.info("  Channel:  %d", WMS_CHANNEL)
        log.info("  PAN ID:   %s", WMS_PAN_ID)
        log.info("  MQTT:     %s:%d", MQTT_SERVER, MQTT_PORT)

        if WMS_PAN_ID.upper() == "FFFF":
            log.warning("WMS_PAN_ID is FFFF — running in discovery mode, blinds will NOT be controlled.")
            log.warning("Follow the instructions in the log to retrieve your network parameters.")

        async with aiomqtt.Client(
            hostname=MQTT_SERVER,
            port=MQTT_PORT,
            username=MQTT_USER,
            password=MQTT_PASSWORD,
        ) as mqtt:
            self.mqtt = mqtt
            log.info("MQTT connected")

            self.stick = WmsStick(
                port=WMS_PORT,
                channel=WMS_CHANNEL,
                pan_id=WMS_PAN_ID,
                key=WMS_KEY,
                on_position=self._on_position,
                on_weather=self._on_weather,
            )

            await self.stick.connect()
            await self.stick.init_network()

            if WMS_PAN_ID.upper() == "FFFF":
                await self._run_discovery_mode()
                return

            # Scan + register blinds
            await self._initial_scan()

            # Subscribe to command topics
            await mqtt.subscribe(f"{STATE_PREFIX}/+/set")
            await mqtt.subscribe(f"{STATE_PREFIX}/+/set_position")
            await mqtt.subscribe(f"{STATE_PREFIX}/+/tilt")
            log.info("Subscribed to command topics")

            # Start polling
            self._poll_task = asyncio.create_task(self._poll_loop())
            if MOVING_INTERVAL > 0:
                self._move_poll_task = asyncio.create_task(self._moving_poll_loop())

            # Process incoming MQTT messages
            async for message in mqtt.messages:
                await self._handle_mqtt(message)

    # ------------------------------------------------------------------
    # Discovery mode
    # ------------------------------------------------------------------

    async def _run_discovery_mode(self):
        SEP = "=" * 62
        log.info(SEP)
        log.info("  WAREMA WMS — NETWORK PARAMETER DISCOVERY MODE")
        log.info(SEP)
        log.info("wms_pan_id is FFFF, so the addon is listening for your")
        log.info("WMS network parameters. Follow these steps on your remote:")
        log.info("")
        log.info("  1. Open the battery cover of the WMS handheld remote.")
        log.info("  2. Press and HOLD the PROG / LEARN button (~5 seconds)")
        log.info("     until the LED flashes rapidly.")
        log.info("  3. Use the remote to select the correct channel.")
        log.info("  4. The remote will scan for the WMS Stick (this addon).")
        log.info("  5. When 'Stick' appears on the remote display, press STOP")
        log.info("     to confirm. The stick is now taught in.")
        log.info("")
        log.info("Waiting up to 180 seconds ...")
        log.info(SEP)

        try:
            params = await self.stick.discover_network_params(timeout_s=180)

            log.info(SEP)
            log.info("  PARAMETERS FOUND — copy these into the addon config:")
            log.info("")
            log.info("    wms_channel : %d",  params["channel"])
            log.info("    wms_pan_id  : %s",  params["pan_id"])
            log.info("    wms_key     : %s",  params["network_key"])
            log.info("")
            log.info("  Then RESTART the addon.")
            log.info(SEP)

            result = {
                "wms_channel": params["channel"],
                "wms_pan_id":  params["pan_id"],
                "wms_key":     params["network_key"],
            }

            # Write to /share/ so the user can read it from the HA file manager
            try:
                with open("/share/warema_params.json", "w") as fh:
                    json.dump(result, fh, indent=2)
                log.info("Parameters saved to /share/warema_params.json")
            except Exception as exc:
                log.warning("Could not write /share/warema_params.json: %s", exc)

            # Publish to MQTT so it shows up in MQTT Explorer / HA MQTT sensors
            try:
                await self.mqtt.publish(
                    "warema/discovery/params", json.dumps(result), retain=True
                )
                log.info("Parameters published to MQTT topic: warema/discovery/params")
            except Exception as exc:
                log.warning("Could not publish params to MQTT: %s", exc)

            # Create a visible notification inside Home Assistant
            await self._notify_ha(
                title="Warema WMS — Network Parameters Found",
                message=(
                    f"**Channel:** {params['channel']}  \n"
                    f"**PAN ID:** {params['pan_id']}  \n"
                    f"**Key:** {params['network_key']}  \n\n"
                    "Copy these into the addon configuration, then restart it."
                ),
                notification_id="warema_wms_discovery",
            )

        except asyncio.TimeoutError:
            log.error("Discovery timed out after 180 s. Please try again.")
            await self._notify_ha(
                title="Warema WMS — Discovery Timed Out",
                message="No network parameters received. Re-run the teach-in steps and restart the addon.",
                notification_id="warema_wms_discovery",
            )
        finally:
            await self.stick.disconnect()

    async def _notify_ha(self, title: str, message: str, notification_id: str):
        """Create a persistent notification in Home Assistant via the Supervisor API."""
        token = os.environ.get("SUPERVISOR_TOKEN", "")
        if not token:
            log.debug("No SUPERVISOR_TOKEN available, skipping HA notification")
            return
        import urllib.request
        import urllib.error
        url = "http://supervisor/core/api/services/persistent_notification/create"
        payload = json.dumps({
            "title": title,
            "message": message,
            "notification_id": notification_id,
        }).encode()
        req = urllib.request.Request(url, data=payload, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        })
        try:
            await asyncio.to_thread(urllib.request.urlopen, req, timeout=5)
            log.info("HA notification created: %s", title)
        except urllib.error.URLError as exc:
            log.debug("Could not reach HA API for notification: %s", exc)
        except Exception as exc:
            log.debug("HA notification error: %s", exc)

    # ------------------------------------------------------------------
    # Initial scan and blind registration
    # ------------------------------------------------------------------

    async def _initial_scan(self):
        log.info("Scanning for WMS devices...")
        try:
            devices = await self.stick.scan_devices()
        except Exception as e:
            log.error("Scan failed: %s", e)
            devices = []

        motorized_types = {"20", "21", "25", "2E"}
        for dev in devices:
            snr = dev["snr"]
            snr_hex = dev["snr_hex"]

            if snr_hex in IGNORED_DEVICES or str(snr) in IGNORED_DEVICES:
                log.info("Ignoring device %s (%s)", snr, snr_hex)
                continue

            if dev["device_type"] not in motorized_types:
                log.debug("Skipping non-motor device %s (%s)", snr, dev["device_type_str"])
                continue

            await self._register_blind(snr, dev["device_type_str"])

        # Force-add devices that may not have responded to scan
        for snr_s, dtype in FORCE_DEVICES.items():
            try:
                snr = int(snr_s)
            except ValueError:
                log.warning("FORCE_DEVICES: invalid SNR '%s', skipping", snr_s)
                continue
            if snr not in self._registered_snrs:
                log.info("Force-adding device SNR %s (type %s)", snr, dtype)
                await self._register_blind(snr, f"Forced motor {snr}")

        if not self._registered_snrs:
            log.warning("No blinds registered. Check your WMS network parameters and device SNRs.")
        else:
            log.info("Registered %d blind(s)", len(self._registered_snrs))

    async def _register_blind(self, snr: int, type_str: str):
        """Add blind to stick, publish discovery, mark as online."""
        name = f"Warema {type_str.strip()} {snr}"
        self.stick.add_blind(snr, name=name)
        self._registered_snrs.add(snr)

        # Clear the retained discovery message first so HA fully re-creates
        # the entity — this picks up any new capabilities (tilt, position
        # slider) that were added in newer addon versions.
        await self.mqtt.publish(topic_discovery(snr), b"", retain=True)
        await asyncio.sleep(0.2)

        # HA autodiscovery
        payload = discovery_payload(snr, name)
        await self.mqtt.publish(
            topic_discovery(snr),
            json.dumps(payload),
            retain=True,
        )
        await self.mqtt.publish(topic_availability(snr), "online", retain=True)
        log.info("Registered blind: %s (SNR %d)", name, snr)

        # Clear any retained messages on command topics left over from previous
        # sessions — empty retained payload removes them from the broker so
        # they are not replayed on the next startup.
        for cmd_topic in (topic_cmd_set(snr), topic_cmd_position(snr), topic_tilt(snr)):
            await self.mqtt.publish(cmd_topic, b"", retain=True)

        # Get initial position and tilt
        try:
            pos = await self.stick.get_position(snr)
            ha_pos = max(0, min(100, 100 - pos["position"]))
            ha_tilt = max(0, min(100, round((pos["angle"] + 100) / 2)))
            await self.mqtt.publish(topic_position(snr), str(ha_pos), retain=True)
            await self.mqtt.publish(topic_tilt_state(snr), str(ha_tilt), retain=True)
        except asyncio.TimeoutError:
            log.warning("Could not get initial position for SNR %d", snr)

    # ------------------------------------------------------------------
    # MQTT command handler
    # ------------------------------------------------------------------

    async def _handle_mqtt(self, message: aiomqtt.Message):
        # Retained messages are replayed by the broker on subscription.
        # Ignore them — acting on stale commands would physically move blinds.
        if message.retain:
            log.debug("Ignoring retained command on %s", message.topic)
            return

        topic = str(message.topic)
        payload = message.payload.decode().strip()
        log.debug("MQTT IN: %s = %s", topic, payload)

        # Extract SNR from topic: warema/<snr>/set or warema/<snr>/set_position
        parts = topic.split("/")
        if len(parts) != 3:
            return
        try:
            snr = int(parts[1])
        except ValueError:
            return

        if snr not in self._registered_snrs:
            log.warning("Command for unknown SNR %d, ignoring", snr)
            return

        cmd = parts[2]

        if cmd == "set":
            if payload == "OPEN":
                log.info("OPEN SNR %d", snr)
                try:
                    await self.stick.set_position(snr, position=0)
                    self._start_tracking(snr)
                except asyncio.TimeoutError:
                    log.warning("Timeout sending OPEN to SNR %d", snr)
            elif payload == "CLOSE":
                log.info("CLOSE SNR %d", snr)
                try:
                    await self.stick.set_position(snr, position=100)
                    self._start_tracking(snr)
                except asyncio.TimeoutError:
                    log.warning("Timeout sending CLOSE to SNR %d", snr)
            elif payload == "STOP":
                log.info("STOP SNR %d", snr)
                try:
                    await self.stick.stop(snr)
                except asyncio.TimeoutError:
                    log.warning("Timeout sending STOP to SNR %d — blind may still be moving", snr)
                self._moving_snrs.discard(snr)

        elif cmd == "set_position":
            try:
                # HA sends position already inverted via set_position_template
                wms_pos = int(payload)
                wms_pos = max(0, min(100, wms_pos))
                log.info("SET_POSITION SNR %d -> %d%%", snr, wms_pos)
                await self.stick.set_position(snr, position=wms_pos)
                self._start_tracking(snr)
            except ValueError:
                log.warning("Invalid position payload: %s", payload)
            except asyncio.TimeoutError:
                log.warning("Timeout sending position %s to SNR %d", payload, snr)

        elif cmd == "tilt":
            try:
                # HA tilt 0-100 → WMS angle pct: 0→-100, 50→0 (horizontal), 100→+100
                ha_tilt = max(0, min(100, int(payload)))
                wms_angle = ha_tilt * 2 - 100
                blind = self.stick.get_blind_state(snr)
                wms_pos = blind.position if (blind and blind.position >= 0) else 0
                log.info("TILT SNR %d -> angle %d (HA tilt %d)", snr, wms_angle, ha_tilt)
                await self.stick.set_position(snr, position=wms_pos, angle=wms_angle)
            except ValueError:
                log.warning("Invalid tilt payload: %s", payload)
            except asyncio.TimeoutError:
                log.warning("Timeout sending tilt to SNR %d", snr)

    # ------------------------------------------------------------------
    # Position + weather callbacks from WmsStick
    # ------------------------------------------------------------------

    def _on_position(self, blind):
        """Called by WmsStick when a position update arrives (including from remote)."""
        snr = blind.snr
        if snr not in self._registered_snrs:
            return
        ha_pos = max(0, min(100, 100 - blind.position))
        ha_tilt = max(0, min(100, round((blind.angle + 100) / 2)))
        log.debug("Position update SNR %d: WMS=%d HA=%d tilt=%d moving=%s",
                  snr, blind.position, ha_pos, ha_tilt, blind.moving)
        asyncio.create_task(
            self.mqtt.publish(topic_position(snr), str(ha_pos), retain=True)
        )
        asyncio.create_task(
            self.mqtt.publish(topic_tilt_state(snr), str(ha_tilt), retain=True)
        )
        if blind.moving:
            # Could be remote-controlled — track it for fast polling
            self._start_tracking(snr)
        else:
            self._moving_snrs.discard(snr)

    def _on_weather(self, weather: dict):
        log.info("Weather: temp=%.1f°C wind=%d lumen=%d rain=%s",
                 weather.get("temp_c", 0),
                 weather.get("wind", 0),
                 weather.get("lumen", 0),
                 weather.get("rain", False))
        # Optionally publish to MQTT for HA sensors
        asyncio.create_task(
            self.mqtt.publish(
                f"{STATE_PREFIX}/weather",
                json.dumps(weather),
                retain=True,
            )
        )

    # ------------------------------------------------------------------
    # Polling loops
    # ------------------------------------------------------------------

    async def _poll_loop(self):
        """Periodically request position from all registered blinds."""
        while True:
            await asyncio.sleep(POLLING_INTERVAL)
            log.debug("Polling positions...")
            for snr in list(self._registered_snrs):
                try:
                    pos = await self.stick.get_position(snr)
                    ha_pos = max(0, min(100, 100 - pos["position"]))
                    ha_tilt = max(0, min(100, round((pos["angle"] + 100) / 2)))
                    await self.mqtt.publish(topic_position(snr), str(ha_pos), retain=True)
                    await self.mqtt.publish(topic_tilt_state(snr), str(ha_tilt), retain=True)
                    if pos["moving"]:
                        self._start_tracking(snr)
                except asyncio.TimeoutError:
                    log.warning("Polling timeout for SNR %d", snr)
                except Exception as e:
                    log.error("Polling error for SNR %d: %s", snr, e)

    async def _moving_poll_loop(self):
        """Poll moving blinds frequently.

        Wakes immediately when _start_tracking() sets _moving_event, so the
        first position update is published without waiting a full MOVING_INTERVAL.
        Falls back to polling at MOVING_INTERVAL to catch remote-triggered moves.
        """
        while True:
            # Wait up to MOVING_INTERVAL, but wake instantly when a command fires
            try:
                await asyncio.wait_for(
                    self._moving_event.wait(), timeout=MOVING_INTERVAL
                )
            except asyncio.TimeoutError:
                pass
            self._moving_event.clear()

            for snr in list(self._moving_snrs):
                try:
                    pos = await self.stick.get_position(snr)
                    ha_pos = max(0, min(100, 100 - pos["position"]))
                    ha_tilt = max(0, min(100, round((pos["angle"] + 100) / 2)))
                    log.debug("Moving SNR %d: WMS=%d HA=%d%% moving=%s",
                              snr, pos["position"], ha_pos, pos["moving"])
                    await self.mqtt.publish(topic_position(snr), str(ha_pos), retain=True)
                    await self.mqtt.publish(topic_tilt_state(snr), str(ha_tilt), retain=True)
                    if not pos["moving"]:
                        log.info("SNR %d stopped at WMS pos %d (HA %d%%)",
                                 snr, pos["position"], ha_pos)
                        self._moving_snrs.discard(snr)
                except Exception as e:
                    log.debug("Moving poll error SNR %d: %s", snr, e)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    bridge = WaremaBridge()
    loop = asyncio.get_event_loop()

    # Graceful shutdown on SIGTERM (Docker stop)
    def _shutdown():
        log.info("Shutdown signal received")
        for task in asyncio.all_tasks(loop):
            task.cancel()

    loop.add_signal_handler(signal.SIGTERM, _shutdown)
    loop.add_signal_handler(signal.SIGINT, _shutdown)

    try:
        await bridge.run()
    except asyncio.CancelledError:
        log.info("Bridge stopped")
    except Exception as e:
        log.critical("Fatal error: %s", e, exc_info=True)
        raise


if __name__ == "__main__":
    asyncio.run(main())
