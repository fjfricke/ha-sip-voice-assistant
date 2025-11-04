"""Caller ID to configuration mapping utilities."""
from typing import Optional, Dict, Any
from app.config import Config


def get_caller_settings(config: Config, caller_id: str) -> Dict[str, Any]:
    """
    Get caller-specific settings (language, instructions, tools) by resolving profiles.
    
    Resolution order:
    1. Get caller config (if exists)
    2. If caller has a profile, use that profile
    3. If caller not found or no profile specified, use default profile
    4. If no default profile exists, use hardcoded defaults
    
    Note: Welcome messages should be included in instructions, e.g.:
    "Start the conversation by saying: Hello, how can I help you?"
    """
    caller_config = config.get_caller_config(caller_id)
    profile_name = None
    profile_config = None
    
    # Try to get profile from caller config
    if caller_config:
        profile_name = caller_config.get("profile")
        if profile_name:
            profile_config = config.get_profile_config(profile_name)
            if profile_config:
                return {
                    "language": profile_config.get("language", "en"),
                    "instructions": profile_config.get("instructions", "You are a helpful assistant."),
                    "available_tools": profile_config.get("available_tools", []),
                }
    
    # Try default profile
    default_profile = config.get_default_profile_config()
    if default_profile:
        return {
            "language": default_profile.get("language", "en"),
            "instructions": default_profile.get("instructions", "You are a helpful assistant."),
            "available_tools": default_profile.get("available_tools", []),
        }
    
    # Fallback to hardcoded defaults
    return {
        "language": "en",
        "instructions": "You are a helpful assistant.",
        "available_tools": [],
    }

