#!/usr/bin/with-contenv bashio

# Get MQTT broker credentials from the Supervisor service.
# Retries 3 times (5 s apart) in case the Mosquitto addon is still starting.
# Requires the HA MQTT integration to be configured once:
#   Settings → Devices & Services → Add Integration → MQTT
MQTT_JSON=""
for attempt in 1 2 3; do
    if MQTT_JSON=$(bashio::services "mqtt" 2>/dev/null) && [ -n "${MQTT_JSON}" ]; then
        bashio::log.info "MQTT: credentials received from Supervisor (attempt ${attempt})"
        break
    fi
    if [ "${attempt}" -lt 3 ]; then
        bashio::log.warning "MQTT: service not ready yet, retrying in 5 s (${attempt}/3)..."
        sleep 5
    fi
done

if [ -n "${MQTT_JSON}" ]; then
    export MQTT_SERVER=$(echo "${MQTT_JSON}" | jq --raw-output '.host  // "core-mosquitto"')
    export MQTT_PORT=$(echo "${MQTT_JSON}"   | jq --raw-output '.port  // 1883')
    export MQTT_USER=$(echo "${MQTT_JSON}"   | jq --raw-output '.username // ""')
    export MQTT_PASSWORD=$(echo "${MQTT_JSON}" | jq --raw-output '.password // ""')
else
    bashio::log.warning "MQTT: Supervisor service unavailable after 3 attempts."
    bashio::log.warning "MQTT: To fix, configure the MQTT integration once in HA:"
    bashio::log.warning "MQTT:   Settings → Devices & Services → Add Integration → MQTT"
    bashio::log.warning "MQTT: Falling back to core-mosquitto without credentials."
    export MQTT_SERVER="core-mosquitto"
    export MQTT_PORT="1883"
    export MQTT_USER=""
    export MQTT_PASSWORD=""
fi

# Allow manual credential override: if mqtt_user is set in the addon config,
# use it (and its password) regardless of what the Supervisor service returned.
_CONF_USER=$(bashio::config 'mqtt_user' '' 2>/dev/null || true)
_CONF_PASS=$(bashio::config 'mqtt_password' '' 2>/dev/null || true)
if [ -n "${_CONF_USER}" ] && [ "${_CONF_USER}" != "null" ]; then
    bashio::log.info "MQTT: using credentials from addon config (manual override)"
    export MQTT_USER="${_CONF_USER}"
    export MQTT_PASSWORD="${_CONF_PASS}"
fi

export WMS_SERIAL_PORT=$(bashio::config 'wms_serial_port')
export WMS_CHANNEL=$(bashio::config 'wms_channel')
export WMS_PAN_ID=$(bashio::config 'wms_pan_id')
export WMS_KEY=$(bashio::config 'wms_key')
export POLLING_INTERVAL=$(bashio::config 'polling_interval')
export MOVING_INTERVAL=$(bashio::config 'moving_interval')
export IGNORED_DEVICES=$(bashio::config 'ignored_devices' '')
export FORCE_DEVICES=$(bashio::config 'force_devices' '')
export LOG_LEVEL=$(bashio::config 'log_level')

exec python3 /app/main.py
