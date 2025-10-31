"""Configuration loading and management."""
import os
import json
from pathlib import Path
from typing import Dict, Any, Optional
import yaml
from dotenv import load_dotenv


class Config:
    """Configuration manager for addon and standalone modes."""
    
    def __init__(self):
        self.is_addon_mode = os.path.exists("/data/options.json")
        self.config: Dict[str, Any] = {}
        self.callers: Dict[str, Any] = {}
        self.tools: Dict[str, Any] = {}
        
        # Load .env file in standalone mode
        if not self.is_addon_mode:
            # Try to load .env from current working directory (ha_sip_voice_assistant/)
            env_path = Path(".env")
            if env_path.exists():
                load_dotenv(env_path)
                print(f"✅ Loaded .env from {env_path.absolute()}")
            else:
                print(f"⚠️  No .env file found at {env_path.absolute()}")
        
    def load(self):
        """Load configuration from addon config or environment variables."""
        if self.is_addon_mode:
            self._load_addon_config()
        else:
            self._load_standalone_config()
        
        # Load YAML configuration files
        caller_config_path = self.config.get("caller_config_path", "config/callers.yaml")
        tools_config_path = self.config.get("tools_config_path", "config/tools.yaml")
        
        self._load_yaml_config(caller_config_path, "callers")
        self._load_yaml_config(tools_config_path, "tools")
    
    def _load_addon_config(self):
        """Load configuration from Home Assistant addon options."""
        with open("/data/options.json", "r") as f:
            self.config = json.load(f)
        
        # In addon mode, try to use supervisor token if no token is configured
        # The supervisor automatically injects the token via SUPERVISOR_TOKEN env var
        if not self.config.get("homeassistant_token"):
            # The Supervisor token is automatically available as environment variable
            # in Home Assistant addon containers
            supervisor_token = os.getenv("SUPERVISOR_TOKEN")
            
            if supervisor_token:
                self.config["homeassistant_token"] = supervisor_token
                print("✅ Using Supervisor token for Home Assistant API access (no manual token needed)")
            else:
                print("⚠️  No Home Assistant token configured and SUPERVISOR_TOKEN not available")
                print("   Please set homeassistant_token in addon options or ensure addon is running in Supervisor")
    
    def _load_standalone_config(self):
        """Load configuration from environment variables."""
        self.config = {
            "sip_server": os.getenv("SIP_SERVER", "192.168.1.1"),
            "sip_username": os.getenv("SIP_USERNAME", ""),
            "sip_password": os.getenv("SIP_PASSWORD", ""),
            "sip_display_name": os.getenv("SIP_DISPLAY_NAME", "HA Voice Assistant"),
            "sip_transport": os.getenv("SIP_TRANSPORT", "udp"),
            "sip_port": int(os.getenv("SIP_PORT", "5060")),
            "openai_api_key": os.getenv("OPENAI_API_KEY", ""),
            "openai_model": os.getenv("OPENAI_MODEL", "gpt-realtime"),
            "homeassistant_url": os.getenv("HOMEASSISTANT_URL", "http://localhost:8123"),
            "homeassistant_token": os.getenv("HOMEASSISTANT_TOKEN", ""),
            "caller_config_path": os.getenv("CALLER_CONFIG_PATH", "config/callers.yaml"),
            "tools_config_path": os.getenv("TOOLS_CONFIG_PATH", "config/tools.yaml"),
        }
    
    def _load_yaml_config(self, config_path: str, key: str):
        """Load YAML configuration file."""
        path = Path(config_path)
        if not path.exists():
            # Try relative to project root
            path = Path(__file__).parent.parent / config_path
        
        if path.exists():
            with open(path, "r") as f:
                data = yaml.safe_load(f)
                if key == "callers":
                    self.callers = data.get("callers", {})
                elif key == "tools":
                    self.tools = data.get("tools", {})
    
    def get_sip_config(self) -> Dict[str, Any]:
        """Get SIP configuration."""
        return {
            "server": self.config["sip_server"],
            "username": self.config["sip_username"],
            "password": self.config["sip_password"],
            "display_name": self.config["sip_display_name"],
            "transport": self.config["sip_transport"],
            "port": self.config["sip_port"],
        }
    
    def get_openai_config(self) -> Dict[str, Any]:
        """Get OpenAI configuration."""
        return {
            "api_key": self.config["openai_api_key"],
            "model": self.config["openai_model"],
        }
    
    def get_homeassistant_config(self) -> Dict[str, Any]:
        """Get Home Assistant configuration."""
        token = self.config.get("homeassistant_token", "")
        
        # In addon mode, if no token configured, try supervisor token as fallback
        if not token and self.is_addon_mode:
            token = os.getenv("SUPERVISOR_TOKEN", "")
        
        return {
            "url": self.config["homeassistant_url"],
            "token": token,
        }
    
    def get_caller_config(self, caller_id: str) -> Optional[Dict[str, Any]]:
        """Get configuration for a specific caller."""
        # Try exact match first
        if caller_id in self.callers:
            return self.callers[caller_id]
        
        # Try without + prefix
        caller_id_no_plus = caller_id.lstrip("+")
        if caller_id_no_plus in self.callers:
            return self.callers[caller_id_no_plus]
        
        # Try with + prefix
        caller_id_with_plus = f"+{caller_id}" if not caller_id.startswith("+") else caller_id
        if caller_id_with_plus in self.callers:
            return self.callers[caller_id_with_plus]
        
        return None
    
    def get_tool_config(self, tool_name: str) -> Optional[Dict[str, Any]]:
        """Get configuration for a specific tool."""
        return self.tools.get(tool_name)
    
    def get_pin(self, caller_id: str) -> Optional[str]:
        """Get PIN for a caller from callers.yaml. Returns None if no PIN is configured."""
        # Get caller config
        caller_config = self.get_caller_config(caller_id)
        if caller_config:
            pin = caller_config.get("pin")
            # Return None if pin is explicitly null or not set
            if pin is not None and pin != "null":
                return str(pin)
        
        # No PIN configured for this caller - return None
        return None

