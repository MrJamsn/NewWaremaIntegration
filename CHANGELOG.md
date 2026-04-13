# Changelog

## [1.0.8] - 2026-04-13
### Fixed
- Blinds no longer move on addon restart. The discovery payload had `retain: true`
  which caused HA to publish OPEN/CLOSE/SET_POSITION/TILT commands with the MQTT
  retain flag. On restart, the broker replayed these retained commands and the
  blinds executed them. Removed `retain` from the discovery payload — state topics
  (position, tilt, availability) are still retained by the addon itself.

## [1.0.7] - 2026-04-13
### Fixed
- Position slider and tilt slider now appear correctly in the HA device card.
  On startup the addon clears the old retained discovery message first, then
  re-publishes the full payload, forcing HA to re-create the entity with all
  current capabilities.
- `set_position_template` used `{{ value }}` instead of `{{ position }}`
  (wrong variable name per HA MQTT cover docs). Fixed to `{{ 100 - position | int }}`.

## [1.0.6] - 2026-04-13
### Added
- Optional `mqtt_user` and `mqtt_password` config fields as a manual credential
  fallback when the Supervisor MQTT service is unavailable.

### Fixed
- MQTT service auto-detection now retries up to 3 times (5 s apart) before
  falling back, to handle race conditions where Mosquitto starts after the addon.
- Improved fallback log message now clearly instructs the user to configure the
  HA MQTT integration (Settings → Devices & Services → Add Integration → MQTT).

## [1.0.5] - 2026-04-10
### Added
- Instant position updates after a command: the moving-poll loop now wakes
  immediately via `asyncio.Event` instead of waiting a full `moving_interval`.
- `moving_interval` default reduced from 2 s to 1 s for snappier feedback.

## [1.0.4] - 2026-04-09
### Changed
- MQTT credentials are now auto-detected from the Supervisor service
  (`bashio::services "mqtt"`). Manual `mqtt_*` config fields removed.
- Fixed `bashio::services "mqtt.host"` dot-notation bug (caused 404 from
  Supervisor API); replaced with `bashio::services "mqtt"` + jq extraction.

## [1.0.3] - 2026-04-08
### Added
- Tilt / slat angle control: HA tilt slider 0–100 maps to WMS angle −100…+100.
- Tilt state reported back to HA after every position poll.

### Fixed
- Position clamping: `pos_hex_to_pct` now clamps output to 0–100 so HA
  never receives an out-of-range value that greys out the open/close arrows.

## [1.0.2] - 2026-04-07
### Fixed
- `repository.json` URL corrected to the real GitHub repository.
- Discovery/onboarding mode improvements: parameters are written to
  `/share/warema_params.json`, published to MQTT, and shown as a
  persistent notification in HA.

## [1.0.1] - 2026-04-06
### Fixed
- Build failure caused by missing `build.json` (empty `BUILD_FROM` arg).

## [1.0.0] - 2026-04-05
### Added
- Initial release: Warema WMS USB stick → MQTT bridge with HA autodiscovery.
- Supports position control (0–100 %), OPEN / CLOSE / STOP commands.
- Periodic polling and fast-poll during movement.
