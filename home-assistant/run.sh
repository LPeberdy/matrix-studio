#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
# Add-on entrypoint. Options are read from /data/options.json by the Python
# process itself (matrix_studio.options) so that the exact same code path runs
# standalone and under pytest; bashio is used only for logging here.
set -euo pipefail

if bashio::config.exists 'log_level'; then
  bashio::log.level "$(bashio::config 'log_level')" || true
fi

bashio::log.info "Starting Matrix Studio"
bashio::log.info "Device endpoint: ws://<home-assistant-host>:$(bashio::config 'ws_port')$(bashio::config 'ws_path')"
bashio::log.info "Scene directory: $(bashio::config 'scenes_dir')"

cd /app
exec python3 -m matrix_studio
