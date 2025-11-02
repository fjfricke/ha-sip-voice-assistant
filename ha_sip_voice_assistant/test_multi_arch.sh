#!/bin/bash
# Test script for building Home Assistant addon for multiple architectures and Alpine versions

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo "🧪 Testing Home Assistant Addon Build - Multi-Architecture & Alpine Versions"
echo "============================================================================"
echo ""

# Check if Docker is available
if ! command -v docker &> /dev/null; then
    echo -e "${RED}❌ Docker is not installed or not in PATH${NC}"
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

echo -e "${GREEN}✅ Docker found${NC}"
echo ""

# Test configurations: (arch, base_image_tag, platform)
TEST_CONFIGS=(
    "armhf-base:3.22|linux/arm/v6"
    "armv7-base:3.22|linux/arm/v7"
    "aarch64-base:3.22|linux/arm64"
    "amd64-base:3.22|linux/amd64"
    "i386-base:3.22|linux/386"
)

# Create a buildx builder if it doesn't exist
BUILDER_NAME="ha-addon-builder"
if ! docker buildx ls | grep -q "$BUILDER_NAME"; then
    echo -e "${BLUE}📦 Creating buildx builder: ${BUILDER_NAME}${NC}"
    docker buildx create --name "$BUILDER_NAME" --use
fi

docker buildx use "$BUILDER_NAME" &> /dev/null

# Track results
PASSED=0
FAILED=0
FAILED_BUILDS=()

echo -e "${BLUE}Starting builds for ${#TEST_CONFIGS[@]} configurations...${NC}"
echo ""

# Test each configuration
for config in "${TEST_CONFIGS[@]}"; do
    IFS='|' read -r base_tag platform <<< "$config"
    BASE_IMAGE="ghcr.io/home-assistant/${base_tag}"
    IMAGE_NAME="ha-sip-voice-assistant-test"
    TAG="${base_tag//:/-}"
    
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo -e "${BLUE}📦 Building: ${base_tag}${NC}"
    echo -e "   Base image: ${BASE_IMAGE}"
    echo -e "   Platform: ${platform}"
    echo ""
    
    START_TIME=$(date +%s)
    
    if docker buildx build \
        --platform "${platform}" \
        --build-arg BUILD_FROM="${BASE_IMAGE}" \
        --tag "${IMAGE_NAME}:${TAG}" \
        --load \
        --progress=plain \
        . 2>&1 | tee "/tmp/build-${base_tag//\//-}.log"; then
        END_TIME=$(date +%s)
        DURATION=$((END_TIME - START_TIME))
        echo ""
        echo -e "${GREEN}✅ Build successful for ${base_tag} (${DURATION}s)${NC}"
        
        # Verify image exists
        if docker image inspect "${IMAGE_NAME}:${TAG}" &> /dev/null; then
            SIZE=$(docker image inspect "${IMAGE_NAME}:${TAG}" --format='{{.Size}}' | numfmt --to=iec-i --suffix=B 2>/dev/null || echo "unknown")
            echo -e "   Image size: ${SIZE}"
            ((PASSED++))
        else
            echo -e "${YELLOW}⚠️  Image created but not found locally (may need --load flag)${NC}"
            ((PASSED++))
        fi
    else
        END_TIME=$(date +%s)
        DURATION=$((END_TIME - START_TIME))
        echo ""
        echo -e "${RED}❌ Build failed for ${base_tag} (${DURATION}s)${NC}"
        FAILED_BUILDS+=("${base_tag}")
        ((FAILED++))
    fi
    echo ""
done

# Summary
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "📊 Build Summary"
echo "================"
echo -e "${GREEN}✅ Passed: ${PASSED}${NC}"
if [ $FAILED -gt 0 ]; then
    echo -e "${RED}❌ Failed: ${FAILED}${NC}"
    echo ""
    echo "Failed builds:"
    for build in "${FAILED_BUILDS[@]}"; do
        echo -e "  ${RED}❌ ${build}${NC}"
        echo -e "     Log: /tmp/build-${build//\//-}.log"
    done
else
    echo -e "${GREEN}❌ Failed: 0${NC}"
fi
echo ""

# List all built images
echo "📦 Built Images:"
docker images "${IMAGE_NAME}" --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}\t{{.CreatedAt}}" | head -20
echo ""

if [ $FAILED -gt 0 ]; then
    exit 1
else
    echo -e "${GREEN}✅ All builds passed!${NC}"
    exit 0
fi
