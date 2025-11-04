"""Caller ID to configuration mapping utilities."""
from typing import Dict, Any
from app.config import Config


def _render_instructions(template: str, name: str) -> str:
    """
    Render instructions template with caller name using Jinja2.
    
    Use Jinja2 syntax in templates, e.g.: {{ name }}
    Example: "Hello {{ name }}, how can I help you?"
    """
    if not template:
        return "You are a helpful assistant."
    
    try:
        from jinja2 import Template
        
        jinja_template = Template(template)
        rendered = jinja_template.render(name=name)
        
        if not rendered or not rendered.strip():
            return "You are a helpful assistant."
        
        return rendered
    except ImportError:
        print("❌ ERROR: jinja2 not installed! Please run: poetry install")
        raise
    except Exception as e:
        print(f"❌ ERROR: Jinja2 rendering failed: {e}")
        import traceback
        traceback.print_exc()
        return "You are a helpful assistant."


def get_caller_settings(config: Config, caller_id: str) -> Dict[str, Any]:
    """
    Get caller-specific settings (language, instructions, tools) by resolving profiles.
    
    Resolution order:
    1. Get caller config (if exists)
    2. If caller has a profile, use that profile
    3. If caller not found or no profile specified, use default profile
    4. If no default profile exists, use hardcoded defaults
    
    Instructions can use Jinja2 templates with {{ name }} syntax.
    
    Note: Welcome messages should be included in instructions, e.g.:
    "Start the conversation by saying: Hello, how can I help you?"
    """
    caller_config = config.get_caller_config(caller_id)
    profile_name = None
    profile_config = None
    caller_name = None
    
    # Get caller name if configured
    if caller_config:
        caller_name = caller_config.get("name", "Guest")
    
    # Default name if not configured
    if not caller_name:
        caller_name = "Guest"
    
    # Try to get profile from caller config
    if caller_config:
        profile_name = caller_config.get("profile")
        if profile_name:
            profile_config = config.get_profile_config(profile_name)
            if profile_config:
                instructions_template = profile_config.get("instructions", "You are a helpful assistant.")
                if not instructions_template:
                    instructions_template = "You are a helpful assistant."
                
                instructions = _render_instructions(instructions_template, caller_name)
                
                if not instructions or not instructions.strip():
                    instructions = "You are a helpful assistant."
                
                return {
                    "language": profile_config.get("language", "en"),
                    "instructions": instructions,
                    "available_tools": profile_config.get("available_tools", []),
                }
    
    # Try default profile
    default_profile = config.get_default_profile_config()
    if default_profile:
        instructions_template = default_profile.get("instructions", "You are a helpful assistant.")
        if not instructions_template:
            instructions_template = "You are a helpful assistant."
        
        instructions = _render_instructions(instructions_template, caller_name)
        
        if not instructions or not instructions.strip():
            instructions = "You are a helpful assistant."
        
        return {
            "language": default_profile.get("language", "en"),
            "instructions": instructions,
            "available_tools": default_profile.get("available_tools", []),
        }
    
    # Fallback to hardcoded defaults
    return {
        "language": "en",
        "instructions": "You are a helpful assistant.",
        "available_tools": [],
    }

