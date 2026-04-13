# Warema WMS Bridge (Python)

Home Assistant addon to control Warema WMS blinds via the WMS USB Stick.

Python reimplementation — no Node.js, no unmaintained npm dependencies.

## Requirements

- Warema WMS USB Stick connected to the HA host
- MQTT broker (e.g. the Mosquitto addon)

## Installation

1. Add this repository to your HA addon store
2. Install "Warema WMS Bridge (Python)"
3. Configure the addon (see below)
4. Start the addon

## Configuration

| Option | Description |
|---|---|
| `mqtt_server` | MQTT broker hostname, e.g. `homeassistant` |
| `mqtt_port` | MQTT port, default `1883` |
| `mqtt_user` | MQTT username (optional) |
| `mqtt_password` | MQTT password (optional) |
| `wms_serial_port` | Serial port of the USB stick, e.g. `/dev/ttyUSB0` |
| `wms_channel` | WMS network channel |
| `wms_pan_id` | WMS PAN ID (set to `FFFF` for discovery mode) |
| `wms_key` | WMS network key |
| `polling_interval` | Seconds between position polls (default: 30) |
| `moving_interval` | Seconds between polls while a blind is moving (default: 2) |
| `ignored_devices` | Comma-separated list of SNRs to ignore |
| `force_devices` | Comma-separated SNRs to add even if not found by scan (format: `SNR` or `SNR:TYPE`) |
| `log_level` | `debug`, `info`, `warning`, `error` |

## Network parameter discovery

If you don't know your WMS_CHANNEL, WMS_PAN_ID and WMS_KEY:

1. Set `wms_pan_id` to `FFFF`
2. Start the addon and watch the log
3. Follow the instructions using your WMS Handheld transmitter
4. Copy the parameters from the log into the config and restart

## Home Assistant integration

Blinds appear automatically as **cover** entities via MQTT autodiscovery.
Position 0 = fully open, 100 = fully closed.

## Credits

Protocol reverse-engineered from the [warema-wms-venetian-blinds](https://www.npmjs.com/package/warema-wms-venetian-blinds) npm package (MIT).
Original research by "Pman" and "willjoha" on the ioBroker forum.
