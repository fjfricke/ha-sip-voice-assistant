#!/bin/bash
# Build Docker image with full logs visible

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Detect architecture for base image
ARCH=$(uname -m)
case "$ARCH" in
    arm64|aarch64)
        BASE_IMAGE="ghcr.io/home-assistant/aarch64-base:latest"
        ;;
    x86_64|amd64)
        BASE_IMAGE="ghcr.io/home-assistant/amd64-base:latest"
        ;;
    *)
        echo "⚠️  Unknown architecture $ARCH, using aarch64"
        BASE_IMAGE="ghcr.io/home-assistant/aarch64-base:latest"
        ;;
esac

IMAGE_NAME="ha-sip-voice-assistant-test"
TAG="local-test"

echo "🔨 Building Docker image with full logs..."
echo "   Base: ${BASE_IMAGE}"
echo "   Image: ${IMAGE_NAME}:${TAG}"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Build with plain progress output (shows all logs)
docker build \
    --progress=plain \
    --build-arg BUILD_FROM="${BASE_IMAGE}" \
    -t "${IMAGE_NAME}:${TAG}" \
    . 2>&1 | tee docker_build.log

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "✅ Build complete! Logs saved to: docker_build.log"
echo "   View logs: cat docker_build.log"
echo "   View last 100 lines: tail -100 docker_build.log"

