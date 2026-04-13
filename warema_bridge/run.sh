#!/usr/bin/with-contenv bashio

# Read MQTT credentials from the HA MQTT service (configured via Mosquitto broker addon)
export MQTT_SERVER=$(bashio::services "mqtt.host")
export MQTT_PORT=$(bashio::services "mqtt.port")
export MQTT_USER=$(bashio::services "mqtt.username")
export MQTT_PASSWORD=$(bashio::services "mqtt.password")

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
