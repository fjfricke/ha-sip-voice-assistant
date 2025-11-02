#!/bin/bash
# Script to build and push pre-built Docker images for Home Assistant addon
# This allows users to install without building on their device

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Configuration
VERSION="${1:-0.1.2}"
REGISTRY="${REGISTRY:-ghcr.io}"
OWNER="${OWNER:-fjfricke}"
IMAGE_NAME="${OWNER}/ha-sip-voice-assistant"

echo -e "${BLUE}📦 Building and Pushing Home Assistant Addon Images${NC}"
echo "=================================================="
echo -e "Version: ${GREEN}${VERSION}${NC}"
echo -e "Registry: ${GREEN}${REGISTRY}${NC}"
echo -e "Image: ${GREEN}${IMAGE_NAME}${NC}"
echo ""

# Check if Docker is available
if ! command -v docker &> /dev/null; then
    echo -e "${RED}❌ Docker is not installed${NC}"
    exit 1
fi

# Check if buildx is available
if ! docker buildx version &> /dev/null; then
    echo -e "${YELLOW}⚠️  Docker buildx not found. Installing...${NC}"
    docker buildx install || {
        echo -e "${RED}❌ Failed to install buildx${NC}"
        exit 1
    }
fi

# Login to registry
echo -e "${BLUE}🔐 Logging in to ${REGISTRY}...${NC}"
if [ "${REGISTRY}" = "ghcr.io" ]; then
    echo -e "${YELLOW}Note: You need a GitHub Personal Access Token with 'write:packages' permission${NC}"
    echo -e "${YELLOW}Create one at: https://github.com/settings/tokens${NC}"
    echo ""
    echo -e "Enter your GitHub username:"
    read -r GITHUB_USER
    echo -e "Enter your GitHub Personal Access Token:"
    read -rs GITHUB_TOKEN
    echo "$GITHUB_TOKEN" | docker login ghcr.io -u "$GITHUB_USER" --password-stdin
else
    docker login "$REGISTRY"
fi

echo ""

# Create buildx builder if needed
BUILDER_NAME="ha-addon-builder"
if ! docker buildx ls | grep -q "$BUILDER_NAME"; then
    echo -e "${BLUE}📦 Creating buildx builder: ${BUILDER_NAME}${NC}"
    docker buildx create --name "$BUILDER_NAME" --use
else
    docker buildx use "$BUILDER_NAME"
fi

# Build configurations
BUILDS=(
    "armhf|ghcr.io/home-assistant/armhf-base:3.22|linux/arm/v6"
    "armv7|ghcr.io/home-assistant/armv7-base:3.22|linux/arm/v7"
    "aarch64|ghcr.io/home-assistant/aarch64-base:3.22|linux/arm64"
    "amd64|ghcr.io/home-assistant/amd64-base:3.22|linux/amd64"
)

echo -e "${BLUE}🔨 Building images...${NC}"
echo ""

for build_config in "${BUILDS[@]}"; do
    IFS='|' read -r arch base platform <<< "$build_config"
    
    IMAGE_TAG="${REGISTRY}/${IMAGE_NAME}-${arch}:${VERSION}"
    IMAGE_LATEST="${REGISTRY}/${IMAGE_NAME}-${arch}:latest"
    
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo -e "${BLUE}📦 Building: ${arch}${NC}"
    echo -e "   Base: ${base}"
    echo -e "   Platform: ${platform}"
    echo -e "   Tags: ${IMAGE_TAG}, ${IMAGE_LATEST}"
    echo ""
    
    START_TIME=$(date +%s)
    
    if docker buildx build \
        --platform "${platform}" \
        --build-arg BUILD_FROM="${base}" \
        --tag "${IMAGE_TAG}" \
        --tag "${IMAGE_LATEST}" \
        --push \
        --progress=plain \
        .; then
        END_TIME=$(date +%s)
        DURATION=$((END_TIME - START_TIME))
        echo ""
        echo -e "${GREEN}✅ Built and pushed ${arch} (${DURATION}s)${NC}"
    else
        echo ""
        echo -e "${RED}❌ Build failed for ${arch}${NC}"
        exit 1
    fi
    echo ""
done

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo -e "${GREEN}✅ All images built and pushed successfully!${NC}"
echo ""
echo "Images pushed:"
for build_config in "${BUILDS[@]}"; do
    IFS='|' read -r arch _ _ <<< "$build_config"
    echo -e "  ${GREEN}✓${NC} ${REGISTRY}/${IMAGE_NAME}-${arch}:${VERSION}"
    echo -e "  ${GREEN}✓${NC} ${REGISTRY}/${IMAGE_NAME}-${arch}:latest"
done
echo ""
echo -e "${BLUE}Next steps:${NC}"
echo "1. Update config.yaml version to match: ${VERSION}"
echo "2. Ensure config.yaml has: image: \"ghcr.io/${OWNER}/ha-sip-voice-assistant-{arch}:${VERSION}\""
echo "3. Commit and push your changes"
echo ""

