#!/usr/bin/with-contenv bash
set -euo pipefail

CONFIG_PATH=/data/options.json

export MQTT_HOST=$(jq -r .mqtt_host ${CONFIG_PATH})
export MQTT_PORT=$(jq -r .mqtt_port ${CONFIG_PATH})
export MQTT_USER=$(jq -r .mqtt_user ${CONFIG_PATH})
export MQTT_PASS=$(jq -r .mqtt_pass ${CONFIG_PATH})
export INTERVAL=$(jq -r .interval_seconds ${CONFIG_PATH})
export SERVERS_JSON=$(jq -c .servers ${CONFIG_PATH})

# Start lightweight web server for optional sidebar access
python3 -m http.server 8099 --directory /app/web &

exec python3 /app/collector.py
