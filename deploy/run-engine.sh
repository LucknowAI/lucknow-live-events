#!/bin/sh
# Entry point for the daily engine run. Cron calls this script.
#
# The private env file supplies DATABASE_URL, ALEMBIC_DATABASE_URL and
# HEALTHCHECK_URL. Override the paths with APP_DIR, ENV_FILE or LOG_DIR.
set -eu
umask 027

APP_DIR="${APP_DIR:-/opt/lucknow-live-events}"
ENV_FILE="${ENV_FILE:-/etc/lucknow-live-events/engine.env}"
LOG_DIR="${LOG_DIR:-/var/log/lucknow-live-events}"

mkdir -p "$LOG_DIR"
# Capture the whole script, so an unreadable env file is not a silent cron failure.
exec >>"$LOG_DIR/engine.log" 2>&1

# Export every variable from the private env file to the engine process.
set -a
. "$ENV_FILE"
set +a

# Unbuffered output: a killed run still leaves its log lines behind.
PYTHONUNBUFFERED=1
export PYTHONUNBUFFERED

cd "$APP_DIR/backend"
exec .venv/bin/python -m engine.run "$@"
