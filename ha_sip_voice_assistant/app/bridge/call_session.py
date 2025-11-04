"""Call session management."""
import asyncio
import random
import socket
import time
import numpy as np
from typing import Dict, Any, Optional
from app.config import Config
from app.sip.rtp_session import RTPSession
from app.sip.audio_bridge import RTPAudioBridge
from app.bridge.audio_adapter import AudioAdapter
from app.ai.openai_client import OpenAIRealtimeClient
from app.ai.tool_handler import ToolHandler
from app.homeassistant.client import HomeAssistantClient
from app.utils.pin_verification import PINVerifier
from app.utils.caller_mapping import get_caller_settings


class CallSession:
    """Manages a single call session from SIP to AI."""
    
    def __init__(
        self,
        config: Config,
        call_id: str,
        caller_id: str,
        call_info: Dict[str, Any],
    ):
        self.config = config
        self.call_id = call_id
        self.caller_id = caller_id
        self.call_info = call_info
        
        # Get caller-specific settings
        caller_settings = get_caller_settings(config, caller_id)
        self.language = caller_settings["language"]
        self.instructions = caller_settings["instructions"]
        self.available_tools = caller_settings["available_tools"]
        
        # Get sample rate from call info (default 8kHz)
        sample_rate = int(self.call_info.get("sample_rate", 8000))
        
        # Components
        self.audio_adapter = AudioAdapter(sample_rate=sample_rate)
        self.rtp_session: Optional[RTPSession] = None
        self.rtp_bridge: Optional[RTPAudioBridge] = None
        self.ai_client: Optional[OpenAIRealtimeClient] = None
        self.ha_client: Optional[HomeAssistantClient] = None
        self.tool_handler: Optional[ToolHandler] = None
        self.pin_verifier: Optional[PINVerifier] = None
        
        # Tasks
        self.uplink_task: Optional[asyncio.Task] = None
        self.ai_receive_task: Optional[asyncio.Task] = None
        self.running = False
        
        # Transcription buffer for PIN verification
        self.transcription_buffer: str = ""
    
    async def start(self):
        """Start the call session."""
        self.running = True
        
        # Initialize Home Assistant client
        self.ha_client = HomeAssistantClient(self.config)
        await self.ha_client.start()
        
        # Initialize PIN verifier
        self.pin_verifier = PINVerifier(self.config)
        
        # Initialize tool handler
        self.tool_handler = ToolHandler(
            self.config,
            self.ha_client,
            self.pin_verifier,
        )
        
        # Build tool definitions for OpenAI
        tools = self._build_tool_definitions()
        
        # Enhance instructions with PIN guidance if needed
        enhanced_instructions = self._enhance_instructions(self.instructions, tools)
        
        # Initialize OpenAI client
        self.ai_client = OpenAIRealtimeClient(
            self.config,
            instructions=enhanced_instructions,
            tools=tools,
            on_audio_received=self._handle_ai_audio,
            on_tool_call=self._handle_tool_call,
            on_transcription=self._handle_transcription,
        )
        
        # Connect to OpenAI
        await self.ai_client.connect()
        
        # Trigger OpenAI to start the conversation (instructions will guide the welcome message)
        await asyncio.sleep(0.5)  # Small delay for session to be ready
        await self.ai_client.request_response()
        
        # Initialize RTP session
        rtp_info = self.call_info.get("rtp_info", {})
        remote_rtp_ip = self.call_info.get("remote_rtp_ip", rtp_info.get("rtp_ip", ""))
        remote_rtp_port = self.call_info.get("remote_rtp_port", rtp_info.get("rtp_port", 0))
        local_rtp_port = self.call_info.get("local_rtp_port", 0)
        # Get local IP from SIP client or use default
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            local_ip = "127.0.0.1"
        
        # Get codec info from call info
        codec_pt = int(self.call_info.get("codec_pt", 0))
        sample_rate = int(self.call_info.get("sample_rate", 8000))
        
        print(f"📞 Starting RTP session: {sample_rate}Hz, PT={codec_pt}")
        
        self.rtp_session = RTPSession(
            local_ip=local_ip,
            local_port=local_rtp_port,
            remote_ip=remote_rtp_ip,
            remote_port=remote_rtp_port,
            ssrc=random.randint(1000000, 9999999),
            sample_rate=sample_rate,
            payload_type=codec_pt,
        )
        
        await self.rtp_session.start()
        
        # Initialize audio bridge
        self.rtp_bridge = RTPAudioBridge(
            self.rtp_session,
            self.audio_adapter,
        )
        
        await self.rtp_bridge.start()
        
        # Start audio streaming tasks
        self.uplink_task = asyncio.create_task(self._uplink_loop())
        self.ai_receive_task = asyncio.create_task(self._ai_receive_loop())
    
    async def stop(self):
        """Stop the call session."""
        self.running = False
        
        # Stop tasks
        if self.uplink_task:
            self.uplink_task.cancel()
            try:
                await self.uplink_task
            except asyncio.CancelledError:
                pass
        
        if self.ai_receive_task:
            self.ai_receive_task.cancel()
            try:
                await self.ai_receive_task
            except asyncio.CancelledError:
                pass
        
        # Stop components
        if self.rtp_bridge:
            await self.rtp_bridge.stop()
        
        if self.rtp_session:
            await self.rtp_session.stop()
        
        if self.ai_client:
            await self.ai_client.disconnect()
        
        if self.ha_client:
            await self.ha_client.stop()
        
        # Reset PIN verifier
        if self.pin_verifier:
            self.tool_handler.reset_call(self.call_id)
    
    def _build_tool_definitions(self) -> list[Dict[str, Any]]:
        """Build tool definitions for OpenAI based on available tools."""
        tools = []
        
        for tool_name in self.available_tools:
            tool_config = self.config.get_tool_config(tool_name)
            if not tool_config:
                continue
            
            # Convert tool config to OpenAI format
            tool_def = {
                "type": "function",
                "name": tool_name,
                "description": tool_config.get("description", ""),
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            }
            
            # Add parameters
            parameters = tool_config.get("parameters", {})
            for param_name, param_config in parameters.items():
                param_type = param_config.get("type", "string")
                tool_def["parameters"]["properties"][param_name] = {
                    "type": param_type,
                    "description": param_config.get("description", ""),
                }
                
                # Add enum if present
                if "enum" in param_config:
                    tool_def["parameters"]["properties"][param_name]["enum"] = param_config["enum"]
            
            # Mark required parameters
            required = [name for name in parameters.keys() if parameters[name].get("required", False)]
            
            # If PIN is required, add PIN as optional parameter
            # OpenAI will ask for it if not provided
            requires_pin = tool_config.get("requires_pin", False)
            if requires_pin:
                # Add PIN parameter (optional - OpenAI will prompt if missing)
                tool_def["parameters"]["properties"]["pin"] = {
                    "type": "integer",
                    "description": "Voice PIN code for authentication as an integer. The user will say this verbally (e.g., 'one one eight three three' for PIN 11833). The voice input will be converted to an integer.",
                }
                # PIN is not required in the schema - OpenAI will ask if missing
                # This allows OpenAI to handle the PIN request flow naturally
            
            # Only add required field if there are required parameters
            # OpenAI Realtime API expects required to be omitted if empty
            if required:
                tool_def["parameters"]["required"] = required
            
            tools.append(tool_def)
            
            # Debug: Print tool definition
            print(f"🔧 Built tool definition: {tool_name}")
            print(f"   Description: {tool_def.get('description', '')[:50]}...")
            print(f"   Parameters: {len(tool_def['parameters']['properties'])} properties")
            if required:
                print(f"   Required: {required}")
        
        return tools
    
    def _enhance_instructions(self, base_instructions: str, tools: list[Dict[str, Any]]) -> str:
        """Enhance instructions with PIN guidance if tools require PIN."""
        # Check if any tool requires PIN
        has_pin_required = False
        for tool in tools:
            tool_name = tool.get("name")
            if tool_name:
                tool_config = self.config.get_tool_config(tool_name)
                if tool_config and tool_config.get("requires_pin", False):
                    has_pin_required = True
                    break
        
        if not has_pin_required:
            return base_instructions
        
        # Add PIN guidance to instructions
        pin_guidance = """

IMPORTANT: Some tools require PIN authentication. When calling a tool that requires a PIN:
1. If the PIN parameter is missing from the tool call arguments, ask the user: "Please provide your PIN code to proceed."
2. The user will speak their PIN code verbally.
3. Extract the PIN from what the user says. The PIN can be any length (not necessarily 4 digits) and might be spoken as digits (e.g., "one one eight three three") or as a number sequence.
4. Call the tool again with the PIN included in the arguments.
5. If the PIN is incorrect, inform the user and ask them to try again.
"""
        
        return base_instructions + pin_guidance
    
    async def _uplink_loop(self):
        """
        Loop: Read from audio adapter, send to OpenAI.
        Following OpenAI's official approach: continuously stream audio,
        let server-side VAD handle turn detection automatically.
        
        IMPORTANT: Send audio continuously without gaps to avoid clicking/popping.
        """
        frame_count = 0
        last_send_time = time.time()
        
        while self.running:
            try:
                # Get audio data (will timeout if queue is empty)
                audio_data = await self.audio_adapter.get_uplink()
                
                # Send immediately - even if it's silence (to maintain continuous stream)
                frame_count += 1
                
                await self.ai_client.send_audio(audio_data)
                # Continuously send audio - OpenAI's server_vad will detect voice activity
                # and automatically handle interruptions when user speaks while AI is speaking
                
                # Maintain timing: send every 20ms
                current_time = time.time()
                elapsed = current_time - last_send_time
                sleep_time = max(0, 0.02 - elapsed)  # 20ms - elapsed time
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
                last_send_time = time.time()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"❌ Error in uplink loop: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(0.01)
    
    async def _ai_receive_loop(self):
        """Loop: Receive from OpenAI (handled by callback)."""
        # The actual receiving is handled by OpenAI client callbacks
        # This task just keeps the loop alive
        while self.running:
            await asyncio.sleep(1)
    
    async def _handle_ai_audio(self, audio_data: bytes):
        """Handle audio received from OpenAI."""
        # Check if AI is still speaking (might have been interrupted)
        if not self.ai_client.is_speaking:
            # AI stopped speaking (might be interrupted) - don't queue audio
            return
        
        # Debug: Log audio chunks
        if not hasattr(self, '_audio_chunk_count'):
            self._audio_chunk_count = 0
        self._audio_chunk_count += 1
        if self._audio_chunk_count % 50 == 0 or self._audio_chunk_count <= 5:
            print(f"🎤 Received from OpenAI: {len(audio_data)} bytes (chunk #{self._audio_chunk_count})")
        
        # Only queue non-empty audio data
        if audio_data and len(audio_data) > 0:
            # Check if this is actual audio (not just silence)
            audio_samples = np.frombuffer(audio_data, dtype=np.int16)
            # If all samples are zero (or very close), it's silence
            if np.any(np.abs(audio_samples) > 10):  # Threshold for "real" audio
                await self.audio_adapter.send_downlink(audio_data)
            # Otherwise, skip silence - the downlink loop will generate silence when needed
    
    def _handle_transcription(self, text: str):
        """Handle transcription from OpenAI."""
        self.transcription_buffer = text
        # Could be used for PIN verification or logging
    
    async def _handle_tool_call(self, tool_call: Dict[str, Any]):
        """Handle tool call from OpenAI."""
        print(f"🔧 _handle_tool_call called with: {tool_call}")
        
        if not self.tool_handler or not self.ai_client:
            print(f"❌ Tool handler or AI client not available! tool_handler={self.tool_handler}, ai_client={self.ai_client}")
            return
        
        tool_call_id = tool_call.get("call_id") or tool_call.get("id", "unknown")
        tool_name = tool_call.get("name", "unknown")
        print(f"🔧 Tool call received: {tool_name} (call_id={tool_call_id})")
        
        # Handle the tool call (PIN comes from tool_call arguments)
        try:
            result = await self.tool_handler.handle_tool_call(
                self.call_id,
                self.caller_id,
                tool_call,
            )
            
            print(f"🔧 Tool call result: success={result.get('success')}, error={result.get('error', 'none')}")
            
            # Submit result back to OpenAI
            if not result.get("success"):
                error = result.get("error", "Unknown error")
                if error == "PIN_REQUIRED":
                    # PIN required - return message asking for PIN
                    await self.ai_client.submit_tool_output(
                        tool_call_id,
                        {"error": "PIN_REQUIRED", "message": "Please provide your PIN code to proceed with this action."}
                    )
                elif error == "PIN_INCORRECT":
                    # PIN incorrect - return error message
                    await self.ai_client.submit_tool_output(
                        tool_call_id,
                        {"error": "PIN_INCORRECT", "message": result.get("message", "The PIN you provided is incorrect. Please try again.")}
                    )
                else:
                    # Other error
                    await self.ai_client.submit_tool_output(
                        tool_call_id,
                        {"error": error, "message": result.get("message", f"Error: {error}")}
                    )
            else:
                # Success - submit result
                tool_result = result.get("result", {})
                await self.ai_client.submit_tool_output(tool_call_id, {
                    "success": True,
                    "result": tool_result,
                })
        except Exception as e:
            print(f"❌ Error handling tool call: {e}")
            import traceback
            traceback.print_exc()
            await self.ai_client.submit_tool_output(tool_call_id, {
                "success": False,
                "error": str(e),
            })

