# Step-by-Step Testing Guide

This guide will help you test the HA SIP Voice Assistant step by step.

## Prerequisites Check

### Step 1: Verify Environment

```bash
# Check Poetry is installed
poetry --version

# Check Python version (should be 3.12+)
poetry run python --version

# Verify dependencies are installed
poetry show
```

### Step 2: Verify Configuration Files

Check that all configuration files exist:
- `config/callers.yaml` - Caller mappings (includes PINs)
- `config/tools.yaml` - Tool definitions

### Step 3: Create Environment File

Create `.env` file with your credentials:
- SIP credentials (FritzBox)
- OpenAI API key
- Home Assistant URL and token (optional for initial testing)

## Testing Steps

### Test 1: Configuration Loading

Test if the configuration loads correctly:
```bash
poetry run python -c "from app.config import Config; c = Config(); c.load(); print('Config loaded successfully')"
```

### Test 2: SIP Client Initialization

Test SIP client creation (without connecting):
```bash
poetry run python -c "
from app.config import Config
from app.sip.client import SIPClient

config = Config()
config.load()
sip_config = config.get_sip_config()
print(f'SIP Server: {sip_config[\"server\"]}')
print(f'SIP Username: {sip_config[\"username\"]}')
print('SIP client can be initialized')
"
```

### Test 3: OpenAI Client Configuration

Test OpenAI configuration:
```bash
poetry run python -c "
from app.config import Config

config = Config()
config.load()
openai_config = config.get_openai_config()
print(f'OpenAI Model: {openai_config[\"model\"]}')
print(f'API Key present: {\"Yes\" if openai_config[\"api_key\"] else \"No\"}')
"
```

### Test 4: Caller Mapping

Test caller configuration loading:
```bash
poetry run python -c "
from app.config import Config
from app.utils.caller_mapping import get_caller_settings

config = Config()
config.load()
settings = get_caller_settings(config, '+1234567890')
print(f'Language: {settings[\"language\"]}')
print(f'Welcome: {settings[\"welcome_message\"]}')
print(f'Tools: {settings[\"available_tools\"]}')
"
```

### Test 5: Tool Configuration

Test tool definitions:
```bash
poetry run python -c "
from app.config import Config

config = Config()
config.load()
tool = config.get_tool_config('light_control')
if tool:
    print(f'Tool: light_control')
    print(f'Service: {tool.get(\"ha_service\")}')
    print(f'Requires PIN: {tool.get(\"requires_pin\")}')
"
```

### Test 6: Home Assistant Client (Optional)

If you have HA configured:
```bash
poetry run python -c "
import asyncio
from app.config import Config
from app.homeassistant.client import HomeAssistantClient

async def test():
    config = Config()
    config.load()
    ha_config = config.get_homeassistant_config()
    print(f'HA URL: {ha_config[\"url\"]}')
    print(f'HA Token present: {\"Yes\" if ha_config[\"token\"] else \"No\"}')
    
    if ha_config['token']:
        client = HomeAssistantClient(config)
        await client.start()
        print('HA client connected')
        await client.stop()

asyncio.run(test())
"
```

### Test 7: Dry Run (No SIP Connection)

Test the application startup without actually connecting:
```bash
poetry run python -m app.main --dry-run
```

### Test 8: Full SIP Registration Test

**⚠️ This will try to register with FritzBox**

Make sure you have:
- Correct SIP credentials
- Network access to FritzBox
- Firewall allows UDP port 5060

```bash
poetry run python -m app.main
```

Watch for:
- "SIP client started"
- "Registered as: username@server"
- Any error messages

## Troubleshooting

### Import Errors
```bash
# Make sure you're in the project root
poetry run python -c "import app; print(app.__file__)"
```

### Configuration Not Found
- Check file paths in `.env` or addon config
- Verify YAML files are valid (no syntax errors)

### SIP Registration Fails
- Verify credentials
- Check network connectivity
- Try pinging FritzBox IP
- Check SIP port (usually 5060)

### OpenAI Connection Issues
- Verify API key is valid
- Check internet connectivity
- Verify API key has access to Realtime API

