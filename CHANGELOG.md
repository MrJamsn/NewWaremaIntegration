# Changelog

## [1.0.20] - 2026-04-23
### Changed
- Tilt buttons for pulse motors (no hardware angle) are now part of the cover
  card instead of separate button entities. All motors now include
  `tilt_command_topic` in their discovery payload. Hardware-tilt motors keep
  `tilt_opened_value: 50` (horizontal slats); pulse motors use
  `tilt_opened_value: 100` and `tilt_closed_value: 0` so the cover card's
  built-in tilt buttons trigger the 0.1 s open/close pulse.
- Old separate MQTT button entities (published in v1.0.16–1.0.19) are removed
  from the broker on restart.

## [1.0.19] - 2026-04-23
### Fixed
- Reverted the v1.0.17 device-type tilt detection. The Wohnzimmer motor (37FC15)
  is also type "20" (Actuator UP) — the same type as the no-tilt motors — so the
  device-type check incorrectly disabled tilt for it. The angle-byte detection from
  v1.0.15 is correct: `angle_hex_to_pct("7F") = 0` (neutral, in range), while
  `angle_hex_to_pct("FF") = 171` (out of range, no tilt hardware).

## [1.0.18] - 2026-04-23
### Fixed
- Open button in HA was disabled when the blind reached 100% (fully open). HA
  disables the button when `current_position >= position_open`. Set `position_open`
  to 101 (a value the bridge never publishes) so the Open button is always enabled;
  the motor's own limit switch handles the mechanical stop safely.

## [1.0.17] - 2026-04-23
### Fixed
- Tilt-capable motors (e.g. Wohnzimmer slat blinds) were sometimes registered as
  non-tilt and given pulse buttons instead of the native tilt slider. Root cause:
  when slats are at the neutral/horizontal position the motor reports angle byte
  `0x7F` (127), which is outside the valid WMS range (−100…+100), so the previous
  angle-based detection concluded no tilt hardware was present. Fixed by using the
  motor type from the device scan instead (`type "20"` = Actuator UP = no tilt
  hardware; all other motorized types are treated as tilt-capable).
- Tilt pulse duration reduced from 0.25 s to 0.1 s per button press.

## [1.0.16] - 2026-04-23
### Changed
- Replaced the tilt slider for motors without physical slat-tilt hardware with
  two dedicated **Tilt Open** / **Tilt Close** button entities on the same HA
  device card. Each button sends a 0.25 s open or close pulse followed by STOP,
  which physically tilts the slats a small amount. This is more reliable than
  the slider, which HA would re-send on state restore or sometimes not fire at all.
- Motors with real WMS angle hardware (e.g. Wohnzimmer slat blinds) continue to
  use the native tilt slider on the cover entity — no change for those.

## [1.0.15] - 2026-04-21
### Changed
- Tilt is now shown in HA for **all** motors, including Actuator UP (type 0x20) motors
  that have no physical slat-tilt hardware.
- Tilt capability is still auto-detected from the first `get_position()` response:
  motors that return a valid WMS angle (−100…+100) use the native WMS angle command
  (existing behavior, e.g. Wohnzimmer blinds); motors that return 0xFF simulate tilt
  via a 0.25 s open/close pulse followed by STOP.
- The tilt topic is now always cleared on startup for all motors.

## [1.0.14] - 2026-04-21
### Fixed
- Tilt slider in HA was permanently stuck at 100% for Warema "Actuator UP"
  motors (type 0x20). These motors always return `0xFF` for the angle byte in
  position reports, which decoded to 171% and clamped to HA 100%.
  The tilt commands themselves *were* executing correctly (confirmed from
  `blind_move_to_pos_response` frames echoing the previous angle).
  Fix: added a per-blind tilt cache. When the angle byte is out of the valid
  WMS range (−100…+100), the last *commanded* HA tilt is used instead. The
  tilt topic is also published immediately after each tilt command so the HA
  slider updates without waiting for the next poll cycle.

## [1.0.13] - 2026-04-19
### Fixed
- Position percentage kept counting down in HA for up to a minute after the
  blind physically stopped. Cause: the Warema motor continues reporting
  `moving=True` while performing final slat micro-adjustments. Added a
  stable-position guard: if the WMS position is unchanged across 3 consecutive
  fast-poll cycles (~3 s), fast-polling stops regardless of the motor's moving
  flag.
- Note: the automatic tilt change that occurs when the blind reaches 100%
  (fully closed) is correct Warema motor behavior — the slats auto-tilt to the
  closed position.

## [1.0.12] - 2026-04-19
### Added
- `wms_position_max` config option (default `100`). Some Warema motors physically
  close at WMS position 50 instead of 100. Setting this to `50` scales all
  intermediate position commands and state reports so that the HA slider matches
  the physical blind travel (e.g. HA 50% → WMS 25 instead of WMS 50).
  OPEN (WMS 0) and CLOSE (WMS position_max) are always correct regardless of
  this setting.

## [1.0.11] - 2026-04-19
### Fixed
- `STOP` sent to the tilt command topic (HA sends it when the stop button is
  pressed) no longer logs a warning — it now correctly calls `stop()` on the
  blind.
- Tilt command no longer drives the blind to position 0 when the cached state
  is stale or unavailable. The handler now calls `get_position()` to fetch the
  actual current position first; falls back to the cached state if that times
  out; skips the command entirely if the position is still unknown.
- Bare `TimeoutError` (raised by `warema_wms` on some Python versions instead
  of `asyncio.TimeoutError`) is now caught in all command handlers and polling
  loops, preventing a fatal bridge crash.

## [1.0.10] - 2026-04-13
### Fixed
- Blinds still moved on restart even after v1.0.9. The commands arriving
  1–4 seconds after subscribing are *live* commands sent by HA to restore its
  last known cover state — not broker-retained messages — so `message.retain`
  was False and the previous check had no effect.
  Added a 6-second startup grace period: all commands received within 6 seconds
  of the initial MQTT subscribe are silently dropped and logged at INFO level.
  The broker-retain check and command-topic clearing (from v1.0.9) are kept as
  additional layers of protection.

## [1.0.9] - 2026-04-13
### Fixed
- Blinds still moved on restart after v1.0.8 because old retained messages
  were already stored on the MQTT broker before the `retain` flag was removed
  from the discovery payload. Two-pronged fix:
  1. On startup, the addon now publishes empty retained messages to each blind's
     command topics (`set`, `set_position`, `tilt`) to remove any stored messages
     from the broker before subscribing.
  2. `_handle_mqtt` now checks `message.retain` and drops any retained message
     delivered on subscription — MQTT sets this flag when replaying stored
     messages to a new subscriber, so commands cannot physically move blinds
     on restart regardless of broker state.

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
