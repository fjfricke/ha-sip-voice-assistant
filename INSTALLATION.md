# Installing and Testing the Add-on on Home Assistant

## Quick Start: Add GitHub Repository (Recommended)

Since your repository is on GitHub at `https://github.com/fjfricke/ha-sip-voice-assistant`, you can add it directly:

### Step 1: Ensure Repository is Up to Date

Make sure all your latest changes are pushed to GitHub:

```bash
cd /Users/felix/Programming/ha-sip-voice-assistant
git add .
git commit -m "Add Dockerfile with all build dependencies"
git push origin main
```

### Step 2: Add Repository to Home Assistant

1. Open Home Assistant web interface
2. Go to **Settings → Add-ons → Add-on Store**
3. Click the three dots (⋮) in the top right corner
4. Select **"Repositories"**
5. Paste this URL:
   ```
   https://github.com/fjfricke/ha-sip-voice-assistant
   ```
6. Click **"Add"**
7. Click **"Close"**

### Step 3: Install the Add-on

1. Refresh the Add-on Store (reload the page or wait a moment)
2. You should see **"HA SIP Voice Assistant Repository"** in the repositories list
3. Look for **"HA SIP Voice Assistant"** in the Add-on Store
4. Click on it
5. Click **"Install"**
6. Wait for installation (this may take 5-10 minutes as it builds the Docker image)

### Step 4: Configure the Add-on

1. Go to **Settings → Add-ons → HA SIP Voice Assistant**
2. Click **"Configuration"**
3. Configure your settings:
   ```yaml
   sip_server: "192.168.1.1"  # Your FritzBox IP
   sip_username: "your_sip_username"
   sip_password: "your_sip_password"
   sip_display_name: "HA Voice Assistant"
   sip_transport: "udp"
   sip_port: 5060
   openai_api_key: "sk-..."  # Your OpenAI API key
   openai_model: "gpt-realtime"
   homeassistant_url: "http://homeassistant:8123"
   homeassistant_token: ""  # Leave empty to use Supervisor token automatically
   caller_config_path: "/config/callers.yaml"
   tools_config_path: "/config/tools.yaml"
   ```
4. Click **"Save"**

### Step 5: Create Configuration Files

Using File Editor add-on or SSH, create these files in `/config`:

**`/config/callers.yaml`:**
```yaml
callers:
  "+1234567890":  # Your phone number
    language: "en"
    instructions: |
      You are a helpful AI assistant for home automation.
      Be precise and friendly.
      When the user wants to control something in the house, use the tools available.
```

**`/config/tools.yaml`:**
```yaml
tools:
  example_tool:
    description: "Example tool - Open a door. This action requires PIN authentication."
    ha_service: "script.example_open_door"
    requires_pin: true
    parameters: {}
```

### Step 6: Start the Add-on

1. Go to **Settings → Add-ons → HA SIP Voice Assistant**
2. Click **"Start"**
3. Check the logs by clicking **"Logs"**
4. You should see: `SIP client started. Waiting for calls...`

## Alternative: Manual Installation (For Testing)

If you want to test locally before pushing to GitHub:

### Step 1: Copy Files to Home Assistant

Using SSH or Samba:

```bash
# Via SSH
cd /Users/felix/Programming/ha-sip-voice-assistant
scp -r ha_sip_voice_assistant homeassistant@your-ha-ip:/config/addons/

# Or create the directory first
ssh homeassistant@your-ha-ip
mkdir -p /config/addons/ha_sip_voice_assistant
# Then use File Editor or Samba to copy the ha_sip_voice_assistant folder
```

### Step 2: Restart Supervisor

1. Go to **Settings → System → Hardware**
2. Click **"Restart Supervisor"**
3. The add-on should appear in **Settings → Add-ons → Local Add-ons**

## Troubleshooting

### Add-on Not Appearing

- Make sure `repository.json` exists in the root of your GitHub repo
- Check that `ha_sip_voice_assistant/config.yaml` exists
- Verify the repository URL is correct

### Build Fails

- Check the Supervisor logs: **Settings → System → Logs**
- Look for Docker build errors
- Ensure you have enough disk space (the build requires ~2GB)

### Add-on Won't Start

- Check the add-on logs
- Verify configuration is valid JSON
- Ensure configuration files (`callers.yaml`, `tools.yaml`) exist in `/config`

### Network Issues

- The add-on needs access to:
  - SIP server (FritzBox) on UDP port 5060
  - OpenAI API (outbound HTTPS)
  - Home Assistant API (internal Docker network)

