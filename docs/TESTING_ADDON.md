# Testing as Home Assistant Addon

This guide explains how to test the addon before deploying to Home Assistant.

## Prerequisites

- Docker installed on your Mac
- Basic understanding of Home Assistant addon structure

## Option 1: Test with Docker (Simulate Addon Environment)

This simulates the Home Assistant addon container environment.

### Step 1: Build the Docker Image

The Dockerfile is in `ha_sip_voice_assistant/` and uses the addon directory as build context:

```bash
cd /Users/felix/Programming/ha-sip-voice-assistant/ha_sip_voice_assistant
docker build -t ha-sip-voice-assistant:test .
```

**Note:** The build context is the `ha_sip_voice_assistant/` directory, which contains `app/`, `config/`, `pyproject.toml`, etc. that the Dockerfile needs.

### Step 2: Create Test Configuration

Create a test directory structure:

```bash
mkdir -p /tmp/ha-addon-test
cd /tmp/ha-addon-test

# Create mock addon options.json
cat > options.json <<EOF
{
  "sip_server": "192.168.178.1",
  "sip_username": "your_sip_username",
  "sip_password": "your_sip_password",
  "sip_display_name": "HA Voice Assistant Test",
  "sip_transport": "udp",
  "sip_port": 5060,
  "openai_api_key": "your_openai_key",
  "openai_model": "gpt-4o-realtime-preview-2024-12-17",
  "homeassistant_url": "http://supervisor/core",
  "homeassistant_token": "",
  "caller_config_path": "/config/callers.yaml",
  "tools_config_path": "/config/tools.yaml"
}
EOF

# Copy config files
cp /Users/felix/Programming/ha-sip-voice-assistant/ha_sip_voice_assistant/config/callers.yaml ./callers.yaml
cp /Users/felix/Programming/ha-sip-voice-assistant/ha_sip_voice_assistant/config/tools.yaml ./tools.yaml
```

### Step 3: Run the Container

```bash
docker run -it --rm \
  --name ha-sip-test \
  -v /tmp/ha-addon-test/options.json:/data/options.json:ro \
  -v /tmp/ha-addon-test:/config:rw \
  -p 5060:5060/udp \
  -p 10000-20000:10000-20000/udp \
  -e SUPERVISOR_TOKEN=your_supervisor_token_if_needed \
  ha-sip-voice-assistant:test
```

**Note:** Replace `your_supervisor_token_if_needed` if you need to test HA API access. In a real addon, this is automatically provided.

### Step 4: Check Logs

The container should:
1. Start successfully
2. Load configuration from `/data/options.json`
3. Register with FritzBox
4. Show "SIP client started. Waiting for calls..."

## Option 2: Test on Real Home Assistant

### Step 1: Copy Addon to Home Assistant

**Important:** You need to copy the **entire project** to Home Assistant, not just the `addon/` directory!

The Dockerfile (`addon/Dockerfile`) uses commands like `COPY app/` and `COPY pyproject.toml`, which means the build context must be the project root (where these files/directories exist).

#### Option A: Copy Everything (Simplest)

```bash
# From your Mac, copy the entire project
cd /Users/felix/Programming/ha-sip-voice-assistant
scp -r . homeassistant@your-ha-ip:/config/addons/ha_sip_voice_assistant/
```

#### Option B: Copy Selectively (Minimum Required)

```bash
# Copy only what's needed for the Docker build
scp -r addon/ app/ config/ pyproject.toml poetry.toml poetry.lock* \
  homeassistant@your-ha-ip:/config/addons/ha_sip_voice_assistant/
```

#### Option C: Via Samba/File Editor

- Connect to `\\homeassistant.local\config` (or use File Editor)
- Navigate to `addons/`
- Create `ha_sip_voice_assistant/` folder
- Copy the following from your project:
  - `addon/` directory (contains Dockerfile, config.yaml, run.sh)
  - `app/` directory (contains all Python application code)
  - `config/` directory (contains default YAML configs)
  - `pyproject.toml`, `poetry.toml`, `poetry.lock` (Poetry dependency files)

**Directory structure in Home Assistant:**
```
/config/addons/ha_sip_voice_assistant/
├── addon/
│   ├── Dockerfile
│   ├── config.yaml
│   └── run.sh
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── ai/
│   ├── bridge/
│   ├── sip/
│   └── ...
├── config/
│   ├── callers.yaml
│   └── tools.yaml
├── pyproject.toml
├── poetry.toml
└── poetry.lock
```

When the Supervisor builds the addon, it runs `docker build` with the addon directory as the working directory, but the build context includes all these files.

### Step 2: Restart Supervisor (Manual Installation Only)

If you installed manually, restart the Home Assistant Supervisor:
- Go to **Settings → System → Hardware**
- Click **"Restart Supervisor"** or restart Home Assistant

The addon should now appear in **Settings → Add-ons → Local Add-ons**.

### Step 3: Create Config Files in Home Assistant

Via File Editor or SSH, create in `/config`:

**`callers.yaml`:**
```yaml
callers:
  "015123507875":
    language: "de"
    instructions: |
      Du bisch en hilfriiche KI-Assistent für d'Huusautomatisierig.
      Sii präzis und fründlich.
      Wänn de Benutzer öppis im Huus steuere wott, bruuch die Tool, wo zur Verfüegig stöh.
      Du redsch Züridüütsch.

      Start s Gspröch mit: „Hoi, wie cha ich Ihne hüt hälfe?“
    available_tools: ["open_wohnungstur", "open_vordereingang"]
    pin: "11833"
```

**`tools.yaml`:**
```yaml
tools:
  open_wohnungstur:
    description: "Open the Wohnungstür (apartment door). This action requires PIN authentication."
    ha_service: "script.open_wohnungstur"
    requires_pin: true
    parameters: {}
  
  open_vordereingang:
    description: "Open the Vordereingang (front entrance door). This action requires PIN authentication."
    ha_service: "script.open_vordereingang"
    requires_pin: true
    parameters: {}
```

### Step 3: Configure Addon in Home Assistant UI

1. Go to **Settings → Add-ons → Local add-ons**
2. Refresh if needed
3. You should see **"HA SIP Voice Assistant"**
4. Click **Install**
5. Configure:
   - **SIP Server**: Your FritzBox IP (e.g., `192.168.178.1`)
   - **SIP Username**: Leave empty (will be assigned by FritzBox)
   - **SIP Password**: Leave empty (will be assigned by FritzBox)
   - **OpenAI API Key**: Your OpenAI key
   - **Home Assistant Token**: Leave empty (uses Supervisor token automatically)
   - **Config paths**: `/config/callers.yaml` and `/config/tools.yaml` (defaults)
6. Click **Save**
7. Click **Start**

### Step 4: Check Addon Logs

1. Go to **Settings → Add-ons → HA SIP Voice Assistant**
2. Click **Logs** tab
3. You should see:
   - Configuration loaded
   - SIP registration process
   - "SIP client started. Waiting for calls..."
   - "✅ SIP registration confirmed and ready for calls"

### Step 5: Test Incoming Call

1. Call the SIP number assigned by FritzBox
2. The addon should:
   - Accept the call
   - Connect to OpenAI
   - Respond with the welcome message
   - Listen for your commands

## Troubleshooting

### Addon doesn't appear in Home Assistant

- Verify addon files are in `/config/addons/ha_sip_voice_assistant/`
- Check `config.yaml` syntax is valid YAML
- Restart Home Assistant or Supervisor
- Check Supervisor logs: `docker logs addon_core_config`

### SIP Registration Fails

**Check logs for:**
- `401 Unauthorized` → Wrong SIP credentials
- `408 Request Timeout` → Network issue or FritzBox not responding
- `Connection refused` → Wrong SIP server IP/port

**Verify:**
- FritzBox IP is correct
- Network connectivity: `ping 192.168.178.1`
- SIP port 5060 is not blocked by firewall

### OpenAI Connection Issues

**Check logs for:**
- `401` → Invalid API key
- `Connection timeout` → Internet connectivity issue

**Verify:**
- OpenAI API key is valid
- API key has access to Realtime API
- Internet connection is working

### Config Files Not Found

**Check logs for:**
- "Configuration file not found" errors

**Verify:**
- Files exist in `/config/` (not `/config/addons/...`)
- File names match exactly: `callers.yaml` and `tools.yaml`
- Files are readable (check permissions)
- YAML syntax is valid

### Home Assistant API Access Fails

**If using Supervisor token (default):**
- Should work automatically
- Verify addon is running in Supervisor (not standalone Docker)

**If using manual token:**
- Create Long-Lived Access Token in HA
- Enter in addon options
- Token must have permission to call services

## Quick Test Commands

### Check if addon is running:
```bash
# Via SSH
docker ps | grep ha_sip
```

### View addon logs:
```bash
# Via SSH
docker logs addon_ha_sip_voice_assistant
# Or follow logs
docker logs -f addon_ha_sip_voice_assistant
```

### Restart addon:
```bash
# Via Home Assistant UI: Settings → Add-ons → HA SIP Voice Assistant → Restart
# Or via SSH:
docker restart addon_ha_sip_voice_assistant
```

## Expected Behavior

When working correctly, you should see:

1. **On Start:**
   ```
   Loading configuration...
   Starting SIP client...
   ✅ SIP registration confirmed and ready for calls
   SIP client started. Waiting for calls...
   ```

2. **On Incoming Call:**
   ```
   📞 Incoming call from 015123507875 (Call-ID: ...)
   ✅ Call session ... started
   ✅ OpenAI session created: ...
   ✅ Tool call: open_wohnungstur (call_id=...)
   ```

3. **During Call:**
   - AI responds to your voice
   - Tool calls are executed when requested
   - PIN verification works for protected tools

