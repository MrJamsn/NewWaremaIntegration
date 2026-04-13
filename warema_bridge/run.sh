#!/usr/bin/with-contenv bashio

# Prefer MQTT credentials from the Supervisor service (Mosquitto broker addon).
# Fall back to manually configured options when the service is not available.
if bashio::services.available "mqtt"; then
    bashio::log.info "MQTT: using credentials from Supervisor service"
    export MQTT_SERVER=$(bashio::services "mqtt.host")
    export MQTT_PORT=$(bashio::services "mqtt.port")
    export MQTT_USER=$(bashio::services "mqtt.username")
    export MQTT_PASSWORD=$(bashio::services "mqtt.password")
else
    bashio::log.warning "MQTT: Supervisor service not available — using manual config"
    _host=$(bashio::config 'mqtt_server')
    export MQTT_SERVER="${_host:-core-mosquitto}"
    export MQTT_PORT=$(bashio::config 'mqtt_port')
    export MQTT_USER=$(bashio::config 'mqtt_user')
    export MQTT_PASSWORD=$(bashio::config 'mqtt_password')
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
