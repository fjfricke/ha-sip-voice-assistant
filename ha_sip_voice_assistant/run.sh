#!/bin/sh
set -e

echo "Starting HA SIP Voice Assistant..."

# Ensure config directory exists
mkdir -p /config

# Copy default configs if they don't exist
if [ ! -f /config/callers.yaml ]; then
    if [ -f /app/config/callers_example.yaml ]; then
        cp /app/config/callers_example.yaml /config/callers.yaml
    fi
fi

if [ ! -f /config/tools.yaml ]; then
    if [ -f /app/config/tools_example.yaml ]; then
        cp /app/config/tools_example.yaml /config/tools.yaml
    fi
fi


# Change to app directory
cd /app || exit 1

# Run the application
exec python3 -m app.main