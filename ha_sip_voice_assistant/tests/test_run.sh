#!/bin/bash
# Test script to run the application and capture output

cd "$(dirname "$0")"

echo "Starting application test..."
echo "Will run for 10 seconds to check SIP registration"
echo "=========================================="

# Kill any existing processes
pkill -f "python -m app.main" 2>/dev/null || true
sleep 1

# Run in background and capture output
poetry run python -m app.main > /tmp/ha_sip_test.log 2>&1 &
APP_PID=$!

echo "Application started (PID: $APP_PID)"
echo "Waiting 10 seconds to check registration..."
sleep 10

# Check if still running
if ps -p $APP_PID > /dev/null 2>&1; then
    echo "✅ Application is still running"
    echo ""
    echo "Output so far:"
    echo "=========================================="
    cat /tmp/ha_sip_test.log
    echo "=========================================="
    echo ""
    echo "Killing application..."
    kill $APP_PID 2>/dev/null || true
    sleep 1
    kill -9 $APP_PID 2>/dev/null || true
else
    echo "⚠️  Application exited early"
    echo ""
    echo "Output:"
    echo "=========================================="
    cat /tmp/ha_sip_test.log
    echo "=========================================="
fi

