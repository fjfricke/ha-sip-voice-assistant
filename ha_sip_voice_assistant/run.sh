#!/bin/sh
set -e

echo "Starting HA SIP Voice Assistant..." >&2

# Ensure config directory exists
mkdir -p /config

# Copy default configs if they don't exist
if [ ! -f /config/callers.yaml ]; then
    if [ -f /app/config/callers_example.yaml ]; then
        cp /app/config/callers_example.yaml /config/callers.yaml
        echo "Created default /config/callers.yaml" >&2
    fi
fi

if [ ! -f /config/tools.yaml ]; then
    if [ -f /app/config/tools_example.yaml ]; then
        cp /app/config/tools_example.yaml /config/tools.yaml
        echo "Created default /config/tools.yaml" >&2
    fi
fi

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