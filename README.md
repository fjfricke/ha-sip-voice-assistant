# Home Assistant SIP Voice Assistant Addon

A Home Assistant addon that registers as a SIP device with your FritzBox, handles incoming calls, and connects them to OpenAI's Realtime API for AI-powered voice interactions. The assistant can trigger Home Assistant services with optional PIN verification.

## Features

- **SIP Client Registration**: Registers as a device with FritzBox and receives calls on an assigned number
- **OpenAI Realtime Integration**: Streams audio to OpenAI Realtime API for natural voice conversations
- **Multilingual Support**: Different languages, welcome messages, and instructions based on caller ID
- **Home Assistant Integration**: Calls HA services through the REST API
- **PIN Verification**: Optional PIN/password verification for sensitive actions (voice + DTMF support)
- **Standalone Mode**: Test on your Mac without Home Assistant

## Repository Structure

This repository is structured as a Home Assistant addon repository:

```
.
├── repository.json          # Repository metadata (for Add-on Store)
├── ha_sip_voice_assistant/  # The actual addon
│   ├── Dockerfile          # Container build instructions
│   ├── config.yaml         # Addon metadata and options
│   ├── run.sh              # Startup script
│   ├── app/                # Application code
│   ├── config/             # Default configuration files
│   ├── pyproject.toml      # Python dependencies
│   └── poetry.lock         # Locked dependencies
├── docs/                   # Documentation
├── tests/                  # Test files
└── README.md               # This file
```

The `ha_sip_voice_assistant/` directory contains everything needed to build and run the addon. When added as a repository to Home Assistant, the Supervisor automatically detects and can install the addon.

## Architecture

The addon uses a pure Python asyncio implementation for SIP/RTP, similar to the [sip-to-ai](https://github.com/aicc2025/sip-to-ai) project. Audio flows bidirectionally:

- **Uplink**: SIP (G.711 μ-law) → RTP → PCM16 → OpenAI Realtime API
- **Downlink**: OpenAI Realtime API → PCM16 → G.711 μ-law → RTP → SIP

## Installation

### As Home Assistant Addon

#### Option A: Add Repository to Addon Store (Recommended)

1. **Add this repository to Home Assistant:**
   - Go to **Settings → Add-ons → Add-on Store**
   - Click the three dots (⋮) in the top right corner
   - Select **"Repositories"**
   - Add the repository URL:
     ```
     https://github.com/yourusername/ha-sip-voice-assistant
     ```
   - Or if using a local repository:
     ```
     https://your-server.com/path/to/repo
     ```
   - Click **"Add"**

2. **Install the addon:**
   - The addon should now appear in the Add-on Store
   - Click on **"HA SIP Voice Assistant"**
   - Click **"Install"**
   - Wait for installation to complete

3. **Configure the addon:**
   - Go to **Settings → Add-ons → HA SIP Voice Assistant**
   - Click **"Configuration"**
   - Configure SIP credentials and OpenAI API key
   - **Note:** Home Assistant token is optional - the addon will automatically use the Supervisor token if not provided
   - Update configuration file paths if needed

4. **Create configuration files in Home Assistant:**
   
   **Option A: Via File Editor (Recommended)**
   - Go to Settings → File Editor (or Developer Tools → File Editor)
   - Navigate to `/config` directory
   - Create `callers.yaml` - Caller mappings (languages, instructions, tools, PINs)
   - Create `tools.yaml` - Tool definitions (HA services, PIN requirements)
   
   **Option B: Via Samba/SSH**
   - Access your Home Assistant `/config` directory via Samba or SSH
   - Create the files directly: `callers.yaml` and `tools.yaml`
   
   **Important:** These files must be in the Home Assistant `/config` directory (same location as `configuration.yaml`).
   The addon reads from `/config/callers.yaml` and `/config/tools.yaml` (as configured in addon options).
   
   **Default configs:** If the files don't exist when the addon starts, it will copy default templates from the Docker image.
   However, you should customize these files with your actual caller numbers, tools, and PINs.
   
   See `docs/ADDON_CONFIG.md` for detailed instructions on creating and managing these files.

5. **Start the addon:**
   - Go back to the addon page
   - Click **"Start"**
   - Check the logs to verify SIP registration

#### Option B: Manual Installation (Legacy)

If you prefer to install manually:

1. Copy the `ha_sip_voice_assistant` directory to your Home Assistant addons folder:
   ```bash
   scp -r ha_sip_voice_assistant/ homeassistant@your-ha-ip:/config/addons/ha_sip_voice_assistant/
   ```

2. Restart Home Assistant Supervisor

3. The addon should appear in **Settings → Add-ons** → **Local Add-ons**

4. Continue with configuration steps above (steps 3-5)

### Standalone Mode (for Mac Testing)

**All work is done from the `ha_sip_voice_assistant/` directory.** This is your working directory.

1. **Navigate to the addon directory:**
```bash
cd ha_sip_voice_assistant
```

2. **Install Poetry if you haven't already:**
```bash
curl -sSL https://install.python-poetry.org | python3 -
```

3. **Install dependencies:**
```bash
poetry install
```

4. **Create a `.env` file:**
```bash
cp .env.example .env
# Edit .env with your configuration
```

5. **Run the application:**
```bash
poetry run python -m app.main
```

Or activate the virtual environment and run normally:
```bash
poetry shell
python -m app.main
```

**Working Directory:**
- All Poetry commands (`poetry install`, `poetry run`, `poetry shell`) should be run from `ha_sip_voice_assistant/`
- The `.env` file should be in `ha_sip_voice_assistant/.env`
- The application automatically loads `.env` when running in standalone mode

## Configuration

### Environment Variables / Addon Options

- `SIP_SERVER` - FritzBox IP address
- `SIP_USERNAME` - SIP account username
- `SIP_PASSWORD` - SIP account password
- `SIP_DISPLAY_NAME` - Display name for SIP registration
- `OPENAI_API_KEY` - OpenAI API key
- `OPENAI_MODEL` - Model name (default: `gpt-realtime`)
- `HOMEASSISTANT_URL` - Home Assistant URL (addon: `http://supervisor/core`, standalone: your HA URL)
- `HOMEASSISTANT_TOKEN` - Long-lived access token (optional in addon mode - will use Supervisor token if not provided)
- `CALLER_CONFIG_PATH` - Path to callers.yaml
- `TOOLS_CONFIG_PATH` - Path to tools.yaml

### Caller Configuration (`callers.yaml`)

```yaml
callers:
  "+1234567890":
    language: "en"
    welcome_message: "Hello, how can I help you?"
    instructions: |
      You are a helpful AI assistant for home automation.
      Be concise and friendly.
    available_tools: ["light_control", "temperature_query"]
  
  "+0987654321":
    language: "de"
    welcome_message: "Hallo, wie kann ich helfen?"
    instructions: |
      Du bist ein hilfreicher KI-Assistent.
      Sei präzise und freundlich.
    available_tools: ["light_control"]
```

### Tool Configuration (`tools.yaml`)

```yaml
tools:
  light_control:
    description: "Turn lights on or off"
    ha_service: "light.toggle"
    requires_pin: true
    parameters:
      entity_id:
        type: "string"
        description: "The light entity ID"
      action:
        type: "string"
        enum: ["on", "off", "toggle"]
```

### PIN Configuration (in `callers.yaml`)

PINs are configured per caller in `callers.yaml`:

```yaml
callers:
  "+1234567890":
    language: "en"
    instructions: "..."
    available_tools: ["light_control"]
    pin: "5678"  # PIN for this caller
  
  "+0987654321":
    language: "de"
    instructions: "..."
    available_tools: ["light_control"]
    pin: null  # No PIN for this caller (tools requiring PIN won't work)
```

## FritzBox Setup

1. In FritzBox, go to Telephony → Internal Numbers
2. Create a new internal number/SIP account
3. Note the username, password, and server IP
4. Configure the addon with these credentials
5. The addon will register and receive calls on this number

## Testing

### Standalone Testing on Mac

1. Configure your `.env` file with SIP credentials
2. Ensure your Mac can reach the FritzBox network
3. Run the application:
```bash
poetry run python -m app.main
```

4. Call the registered number from another phone
5. The AI assistant should answer and respond

### Testing Home Assistant Integration

1. Ensure Home Assistant is accessible
2. Create a long-lived access token in Home Assistant
3. Configure `HOMEASSISTANT_URL` and `HOMEASSISTANT_TOKEN`
4. Test by asking the AI to control a light or switch

## Tool Execution Flow

1. User asks AI to perform an action (e.g., "Turn on the living room light")
2. AI recognizes intent and calls the appropriate tool
3. If PIN is required:
   - AI prompts user for PIN
   - User can provide PIN via voice or DTMF tones
   - PIN is verified against configuration
4. If verified (or no PIN required), tool executes:
   - Home Assistant service is called via REST API
   - Result is returned to AI
   - AI responds to user with confirmation

## PIN Verification

The addon supports two methods for PIN entry:

1. **Voice**: User speaks the PIN (e.g., "one two three four" or "1234")
2. **DTMF**: User presses digits on their phone keypad

The system tries voice first, then falls back to DTMF if needed.

## Troubleshooting

### SIP Registration Fails

- Check FritzBox SIP credentials
- Ensure network connectivity
- Check firewall settings (UDP port 5060)
- Verify SIP username/password

### No Audio

- Check RTP ports (10000-20000 UDP) are accessible
- Verify G.711 codec support
- Check OpenAI API key and connection

### Tool Calls Fail

- Verify Home Assistant token has appropriate permissions
- Check entity IDs exist
- Review tool configuration in `tools.yaml`

### PIN Verification Fails

- Verify PIN configuration in `callers.yaml`
- Check caller ID format matches configuration
- Test both voice and DTMF methods

## License

Apache License 2.0

