"""Caller ID to configuration mapping utilities."""
from typing import Optional, Dict, Any
from app.config import Config


def get_caller_settings(config: Config, caller_id: str) -> Dict[str, Any]:
    """
    Get caller-specific settings (language, instructions, tools).
    
    Returns default English settings if caller not found.
    Note: Welcome messages should be included in instructions, e.g.:
    "Start the conversation by saying: Hello, how can I help you?"
    """
    caller_config = config.get_caller_config(caller_id)
    
    if caller_config:
        return {
            "language": caller_config.get("language", "en"),
            "instructions": caller_config.get("instructions", "You are a helpful assistant."),
            "available_tools": caller_config.get("available_tools", []),
        }
    
    # Default settings
    return {
        "language": "en",
        "instructions": "You are a helpful assistant.",
        "available_tools": [],
    }

