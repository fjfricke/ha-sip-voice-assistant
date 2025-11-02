"""Tool calling handler with PIN verification."""
from typing import Dict, Any, Optional, Callable, Awaitable
from app.config import Config
from app.utils.pin_verification import PINVerifier
from app.homeassistant.client import HomeAssistantClient


class ToolHandler:
    """Handles tool calls from OpenAI with PIN verification."""
    
    def __init__(
        self,
        config: Config,
        ha_client: HomeAssistantClient,
        pin_verifier: PINVerifier,
        on_pin_prompt: Optional[Callable[[], Awaitable[str]]] = None,
    ):
        self.config = config
        self.ha_client = ha_client
        self.pin_verifier = pin_verifier
        self.on_pin_prompt = on_pin_prompt
        
        # Track PIN verification state per call
        self.pin_verified: Dict[str, bool] = {}  # call_id -> verified
        self.pending_tool_calls: Dict[str, Dict[str, Any]] = {}  # call_id -> tool_call
    
    async def handle_tool_call(
        self,
        call_id: str,
        caller_id: str,
        tool_call: Dict[str, Any],
        voice_text: Optional[str] = None,
        dtmf_digit: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Handle a tool call from OpenAI.
        PIN verification is now handled directly in the tool call arguments.
        
        Args:
            call_id: Unique call identifier
            caller_id: Caller's phone number
            tool_call: Tool call from OpenAI with name and arguments (including PIN if required)
            voice_text: Deprecated - PIN comes from tool_call arguments
            dtmf_digit: Deprecated - only voice-based PIN supported
        
        Returns:
            Tool call result
        """
        tool_name = tool_call.get("name")
        arguments = tool_call.get("arguments", {})
        
        # Get tool configuration
        tool_config = self.config.get_tool_config(tool_name)
        if not tool_config:
            return {
                "success": False,
                "error": f"Unknown tool: {tool_name}",
            }
        
        # Check if PIN is required
        requires_pin = tool_config.get("requires_pin", False)
        
        if requires_pin:
            # Get PIN from tool call arguments (voice-based via Realtime API)
            # OpenAI returns it as an integer per the tool schema ("type": "integer")
            provided_pin = arguments.get("pin")
            
            if provided_pin is None:
                # PIN not provided - OpenAI will ask for it
                return {
                    "success": False,
                    "error": "PIN_REQUIRED",
                    "message": "Please provide your PIN code to proceed with this action.",
                }
            
            # Convert to int (OpenAI should provide as int per schema, but handle string representations)
            if not isinstance(provided_pin, int):
                try:
                    provided_pin_int = int(provided_pin)
                except (ValueError, TypeError):
                    return {
                        "success": False,
                        "error": "PIN_INCORRECT",
                        "message": "The PIN format is invalid. Please provide your PIN as a number.",
                    }
            else:
                provided_pin_int = provided_pin
            
            # Get expected PIN for this caller (already an int)
            expected_pin = self.pin_verifier.get_expected_pin(caller_id)
            if not expected_pin:
                # No PIN configured for this caller - tool cannot be used
                return {
                    "success": False,
                    "error": "PIN_NOT_CONFIGURED",
                    "message": "This action requires a PIN, but no PIN is configured for your phone number. The action cannot be performed.",
                }
            
            # Simple integer comparison - OpenAI returns PIN as integer
            verified = (provided_pin_int == expected_pin)
            
            if not verified:
                # PIN incorrect - OpenAI will ask again
                return {
                    "success": False,
                    "error": "PIN_INCORRECT",
                    "message": "The PIN you provided is incorrect. Please try again.",
                }
            
            # PIN verified - remove it from arguments before executing tool
            arguments.pop("pin", None)
        
        # Execute the tool
        try:
            result = await self._execute_tool(tool_config, arguments)
            return {
                "success": True,
                "result": result,
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
            }
    
    async def _execute_tool(self, tool_config: Dict[str, Any], arguments: Dict[str, Any]) -> Any:
        """Execute a tool by calling the Home Assistant service."""
        ha_service = tool_config.get("ha_service")
        if not ha_service:
            raise ValueError("Tool configuration missing ha_service")
        
        print(f"🔧 Executing tool: {ha_service} with arguments: {arguments}")
        
        # Parse service (e.g., "script.open_wohnungstur" -> domain="script", service="open_wohnungstur")
        if "." not in ha_service:
            raise ValueError(f"Invalid ha_service format: {ha_service}")
        
        domain, service = ha_service.split(".", 1)
        
        # Extract entity_id from arguments (if present)
        entity_id = arguments.get("entity_id")
        
        # Get parameters from tool config
        parameters = tool_config.get("parameters", {})
        
        # Build service data from arguments
        # For scripts, we typically don't need entity_id - the service name IS the script
        service_data = {}
        for key, value in arguments.items():
            # Skip PIN and entity_id
            if key == "pin" or key == "entity_id":
                continue
            if key in parameters:
                service_data[key] = value
        
        # For scripts in HA, the service name IS the script name
        # Example: script.open_wohnungstur -> domain="script", service="open_wohnungstur"
        # We call: POST /api/services/script/open_wohnungstur with empty body (no entity_id needed)
        if domain == "script":
            # Scripts don't use entity_id - the service name is the script
            entity_id = None  # Scripts don't need entity_id
        
        print(f"🔧 Calling HA service: domain={domain}, service={service}, entity_id={entity_id}, service_data={service_data}")
        
        # Call Home Assistant service
        try:
            result = await self.ha_client.call_service(
                domain=domain,
                service=service,
                entity_id=entity_id,
                **service_data
            )
            print(f"🔧 HA service call successful: {result}")
            return result
        except Exception as e:
            print(f"❌ HA service call failed: {e}")
            raise
    
    def reset_call(self, call_id: str):
        """Reset PIN verification state for a call."""
        self.pin_verified.pop(call_id, None)
        self.pending_tool_calls.pop(call_id, None)
        self.pin_verifier.reset()

