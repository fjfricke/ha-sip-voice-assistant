# Testing the Addon Locally

This guide explains how to test the Home Assistant addon build locally before deploying.

## Quick Test

Run the automated test script:

```bash
cd ha_sip_voice_assistant
./test_build.sh
```

This will:
1. ✅ Check Docker is installed
2. ✅ Build the Docker image
3. ✅ Verify the image was created
4. ✅ Test basic container functionality

## Manual Testing

### Step 1: Build the Docker Image

```bash
cd ha_sip_voice_assistant

# For your architecture (e.g., aarch64 for Apple Silicon Mac)
docker build \
  --build-arg BUILD_FROM=ghcr.io/home-assistant/aarch64-base:latest \
  -t ha-sip-voice-assistant:test \
  .
```

**Note:** The base image depends on your system:
- **aarch64** (Apple Silicon M1/M2/M3): `ghcr.io/home-assistant/aarch64-base:latest`
- **amd64** (Intel Mac/PC): `ghcr.io/home-assistant/amd64-base:latest`
- **armv7** (Raspberry Pi): `ghcr.io/home-assistant/armv7-base:latest`

### Step 2: Test Basic Container

```bash
# Test that the container can start and imports work
docker run --rm ha-sip-voice-assistant:test python3 -c "import app; print('✅ Imports OK')"
```

### Step 3: Test with Mock Configuration

Create a test configuration:

```bash
mkdir -p /tmp/test-addon
cat > /tmp/test-addon/options.json <<EOF
{
  "sip_server": "192.168.1.1",
  "sip_username": "test",
  "sip_password": "test",
  "sip_display_name": "Test Assistant",
  "sip_transport": "udp",
  "sip_port": 5060,
  "openai_api_key": "test-key",
  "openai_model": "gpt-realtime",
  "homeassistant_url": "http://homeassistant:8123",
  "homeassistant_token": "",
  "caller_config_path": "/config/callers.yaml",
  "tools_config_path": "/config/tools.yaml"
}
EOF

# Copy example configs
cp config/callers_example.yaml /tmp/test-addon/callers.yaml
cp config/tools_example.yaml /tmp/test-addon/tools.yaml
```

Run with test config:

```bash
docker run -it --rm \
  --name ha-sip-test \
  -v /tmp/test-addon/options.json:/data/options.json:ro \
  -v /tmp/test-addon:/config:rw \
  -p 5060:5060/udp \
  ha-sip-voice-assistant:test
```

**Note:** This won't actually connect to SIP/OpenAI, but it will test:
- ✅ Configuration loading
- ✅ File structure
- ✅ Python imports
- ✅ Basic startup

### Step 4: Verify Image Size

Check that the image is reasonable:

```bash
docker images ha-sip-voice-assistant:test
```

Expected size: ~200-500MB (depends on dependencies)

## Testing Architecture-Specific Builds

Home Assistant builds for multiple architectures. Test the build process for each:

```bash
# Test ARM64 (Apple Silicon)
docker build \
  --platform linux/arm64 \
  --build-arg BUILD_FROM=ghcr.io/home-assistant/aarch64-base:latest \
  -t ha-sip-voice-assistant:arm64 \
  .

# Test AMD64 (Intel)
docker build \
  --platform linux/amd64 \
  --build-arg BUILD_FROM=ghcr.io/home-assistant/amd64-base:latest \
  -t ha-sip-voice-assistant:amd64 \
  .
```

## Common Build Issues

### Issue: Poetry installation fails

**Error:** `curl: (6) Could not resolve host: install.python-poetry.org`

**Solution:** Check internet connectivity. Poetry installer needs internet access.

### Issue: Missing dependencies

**Error:** `ERROR: Could not find a version that satisfies the requirement`

**Solution:** 
- Check `pyproject.toml` for valid package versions
- Run `poetry lock --no-update` to regenerate `poetry.lock`

### Issue: COPY fails

**Error:** `COPY failed: file not found`

**Solution:** Ensure you're running `docker build` from the `ha_sip_voice_assistant/` directory (where Dockerfile is located).

### Issue: Base image not found

**Error:** `pull access denied for ghcr.io/home-assistant/...`

**Solution:** The base images are public, but check your Docker can access ghcr.io. Try:
```bash
docker pull ghcr.io/home-assistant/aarch64-base:latest
```

## Next Steps

Once local build works:

1. ✅ Commit and push your changes
2. ✅ Add repository to Home Assistant
3. ✅ Install from Add-on Store
4. ✅ Monitor build logs in Home Assistant

## See Also

- `docs/TESTING_ADDON.md` - Full testing guide including Home Assistant deployment
- `docs/ADDON_CONFIG.md` - Configuration file guide

