#!/usr/bin/with-contenv bashio

# Get MQTT broker credentials from the Supervisor service.
# bashio::services "mqtt"  →  GET /services/mqtt  (returns full JSON object)
# bashio::services "mqtt.host"  →  wrongly calls GET /services/mqtt.host  (404)
# So we query the whole object once and extract fields with jq.
if MQTT_JSON=$(bashio::services "mqtt" 2>/dev/null) && [ -n "${MQTT_JSON}" ]; then
    bashio::log.info "MQTT: credentials received from Supervisor service"
    export MQTT_SERVER=$(echo "${MQTT_JSON}" | jq --raw-output '.host  // "core-mosquitto"')
    export MQTT_PORT=$(echo "${MQTT_JSON}"   | jq --raw-output '.port  // 1883')
    export MQTT_USER=$(echo "${MQTT_JSON}"   | jq --raw-output '.username // ""')
    export MQTT_PASSWORD=$(echo "${MQTT_JSON}" | jq --raw-output '.password // ""')
else
    bashio::log.warning "MQTT: Supervisor service not available — will attempt core-mosquitto"
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
