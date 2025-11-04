#!/usr/bin/with-contenv bashio
set -e

echo "Starting HA SIP Voice Assistant..." >&2

# Change to app directory
cd /app || exit 1

echo "Current directory: $(pwd)" >&2
echo "Python version: $(python3 --version)" >&2
echo "Starting application..." >&2

# Run the application with unbuffered output
# PYTHONUNBUFFERED=1 ensures print() statements are immediately visible
# -u flag also disables buffering
# Redirect stderr to stdout so errors are visible
export PYTHONUNBUFFERED=1
exec python3 -u -m app.main 2>&1