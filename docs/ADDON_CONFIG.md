# Addon Configuration Guide

## Configuration File Locations

When running as a Home Assistant Addon, configuration files should be placed in the **Home Assistant `/config` directory**.

This is the same directory where your `configuration.yaml` file is located.

### Required Files

Create these files in `/config`:

- **`callers.yaml`** - Caller-specific settings (language, instructions, tools, PINs)
- **`tools.yaml`** - Tool definitions (Home Assistant services, PIN requirements)

### How to Create the Files

#### Option 1: Using Home Assistant File Editor

1. Go to **Settings → Add-ons → File Editor**
2. Or use **Developer Tools → File Editor** in the sidebar
3. Navigate to `/config` directory
4. Click **"Create new file"** or use the editor
5. Create `callers.yaml` and `tools.yaml`

#### Option 2: Via Samba (Network Share)

1. Enable Samba addon in Home Assistant
2. Connect to your Home Assistant via network share (e.g., `\\homeassistant.local\config`)
3. Create the files directly in the `config` folder

#### Option 3: Via SSH

1. Enable SSH addon or use SSH access
2. Navigate to `/config` directory
3. Create files using your preferred editor:
   ```bash
   nano /config/callers.yaml
   nano /config/tools.yaml
   ```

### Default Configs

If you don't create these files, the addon will automatically copy default templates from the Docker image to `/config/` on first start.

However, **you should customize these files** with your actual caller numbers, tools, and PINs.

### File Paths in Addon Options

The addon options in `addon/config.yaml` define the default paths:
- `caller_config_path: "/config/callers.yaml"`
- `tools_config_path: "/config/tools.yaml"`

You can change these paths in the addon configuration UI if needed, but `/config` is the standard location.

### Example: Creating callers.yaml

```yaml
callers:
  "015123507875":
    language: "de"
    instructions: |
      Du bisch en hilfriiche KI-Assistent für d'Huusautomatisierig.
      Sii präzis und fründlich.
    available_tools: ["open_wohnungstur", "open_vordereingang"]
    pin: "11833"
```

### Verifying Files

After creating the files, you can verify they exist:

1. In File Editor, navigate to `/config`
2. You should see `callers.yaml` and `tools.yaml`
3. Or via SSH:
   ```bash
   ls -la /config/callers.yaml /config/tools.yaml
   ```

### Troubleshooting

**Problem: Addon can't find config files**

- Check that files are in `/config` (not `/config/ha_sip_voice_assistant/`)
- Verify file names match exactly: `callers.yaml` and `tools.yaml`
- Check file permissions (should be readable by addon user)
- Review addon logs for file path errors

**Problem: Config changes not taking effect**

- Restart the addon after making changes
- Check YAML syntax for errors (use YAML validator)
- Review addon logs for parsing errors

