#!/usr/bin/with-contenv bashio

export MQTT_SERVER=$(bashio::config 'mqtt_server')
export MQTT_PORT=$(bashio::config 'mqtt_port')
export MQTT_USER=$(bashio::config 'mqtt_user')
export MQTT_PASSWORD=$(bashio::config 'mqtt_password')
export WMS_SERIAL_PORT=$(bashio::config 'wms_serial_port')
export WMS_CHANNEL=$(bashio::config 'wms_channel')
export WMS_PAN_ID=$(bashio::config 'wms_pan_id')
export WMS_KEY=$(bashio::config 'wms_key')
export POLLING_INTERVAL=$(bashio::config 'polling_interval')
export MOVING_INTERVAL=$(bashio::config 'moving_interval')
export IGNORED_DEVICES=$(bashio::config 'ignored_devices')
export FORCE_DEVICES=$(bashio::config 'force_devices')
export LOG_LEVEL=$(bashio::config 'log_level')

exec python3 /app/main.py
