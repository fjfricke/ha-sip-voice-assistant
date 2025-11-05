"""Call session management."""
import asyncio
import time
import numpy as np
from typing import Dict, Any, Optional
from app.config import Config
from app.bridge.audio_adapter import AudioAdapter
from app.ai.openai_client import OpenAIRealtimeClient
from app.ai.tool_handler import ToolHandler
from app.homeassistant.client import HomeAssistantClient
from app.utils.pin_verification import PINVerifier
from app.utils.caller_mapping import get_caller_settings

# Try to import pyVoIP call object
try:
    from pyVoIP.VoIP import VoIPCall
    PYVOIP_AVAILABLE = True
except ImportError:
    PYVOIP_AVAILABLE = False
    VoIPCall = None


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
        self.voip_call: Optional[VoIPCall] = call_info.get("voip_call") if PYVOIP_AVAILABLE else None
        self.ai_client: Optional[OpenAIRealtimeClient] = None
        self.ha_client: Optional[HomeAssistantClient] = None
        self.tool_handler: Optional[ToolHandler] = None
        self.pin_verifier: Optional[PINVerifier] = None
        
        # Tasks
        self.uplink_task: Optional[asyncio.Task] = None
        self.downlink_task: Optional[asyncio.Task] = None
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
        
        # Validate instructions before enhancing
        if not self.instructions or not self.instructions.strip():
            self.instructions = "You are a helpful assistant."
        
        # Enhance instructions with PIN guidance if needed
        enhanced_instructions = self._enhance_instructions(self.instructions, tools)
        
        # Validate enhanced instructions
        if not enhanced_instructions or not enhanced_instructions.strip():
            enhanced_instructions = "You are a helpful assistant."
        
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
        
        # Answer the call if using pyVoIP
        if self.voip_call:
            try:
                self.voip_call.answer()
                print("✅ Answered pyVoIP call")
            except Exception as e:
                print(f"❌ Error answering call: {e}")
                import traceback
                traceback.print_exc()
        
        # Start audio streaming tasks
        # Uplink: pyVoIP call -> AudioAdapter -> OpenAI
        self.uplink_task = asyncio.create_task(self._uplink_loop())
        # Downlink: OpenAI -> AudioAdapter -> pyVoIP call
        self.downlink_task = asyncio.create_task(self._downlink_loop())
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
        
        if self.downlink_task:
            self.downlink_task.cancel()
            try:
                await self.downlink_task
            except asyncio.CancelledError:
                pass
        
        if self.ai_receive_task:
            self.ai_receive_task.cancel()
            try:
                await self.ai_receive_task
            except asyncio.CancelledError:
                pass
        
        # Hangup call if using pyVoIP
        if self.voip_call:
            try:
                self.voip_call.hangup()
                print("✅ Hung up pyVoIP call")
            except Exception as e:
                print(f"⚠️  Error hanging up call: {e}")
        
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
        Loop: Read from pyVoIP call, convert via AudioAdapter, send to OpenAI.
        Using AudioAdapter which worked in the original implementation.
        """
        frame_interval = 0.02  # 20ms
        
        while self.running:
            try:
                loop_start = time.time()
                
                # Read audio from pyVoIP call (160 bytes = 20ms at 8kHz, 8-bit PCM)
                if self.voip_call:
                    try:
                        # Read audio in executor since pyVoIP is synchronous
                        # Use blocking=True to ensure we get complete frames
                        audio_8bit = await asyncio.get_event_loop().run_in_executor(
                            None, 
                            lambda: self.voip_call.read_audio(length=160, blocking=True)
                        )
                        
                        if audio_8bit and len(audio_8bit) == 160:
                            # Convert 8-bit unsigned PCM to 16-bit signed PCM
                            # pyVoIP uses 8-bit unsigned (0-255), we need 16-bit signed (-32768 to 32767)
                            # Method: Convert 8-bit to 16-bit by scaling and centering
                            samples_8bit = np.frombuffer(audio_8bit, dtype=np.uint8)
                            # Convert: 0-255 -> -32768 to 32767
                            # Formula: (sample - 128) * 256
                            samples_16bit = ((samples_8bit.astype(np.int16) - 128) * 256).astype(np.int16)
                            pcm16_8k = samples_16bit.tobytes()
                            
                            # Verify: 160 samples * 2 bytes = 320 bytes
                            if len(pcm16_8k) != 320:
                                print(f"⚠️  Unexpected PCM16 size: {len(pcm16_8k)} bytes (expected 320)")
                                # Pad or truncate
                                if len(pcm16_8k) < 320:
                                    pcm16_8k = pcm16_8k + b'\x00' * (320 - len(pcm16_8k))
                                else:
                                    pcm16_8k = pcm16_8k[:320]
                            
                            # Send to audio adapter (will resample to 24kHz for OpenAI)
                            await self.audio_adapter.send_uplink(pcm16_8k)
                        else:
                            # Send silence
                            await self.audio_adapter.send_uplink(b'\x00' * 320)
                    except Exception as e:
                        # Call might have ended
                        print(f"⚠️  Error reading audio from call: {e}")
                        self.running = False
                        break
                else:
                    # No voip_call, send silence
                    await self.audio_adapter.send_uplink(b'\x00' * 320)
                
                # Get audio data from adapter (resampled to 24kHz)
                audio_data = await self.audio_adapter.get_uplink()
                
                # Send to OpenAI (even if silence, to maintain continuous stream)
                await self.ai_client.send_audio(audio_data)
                
                # Maintain precise 20ms frame timing
                elapsed = time.time() - loop_start
                sleep_time = max(0, frame_interval - elapsed)
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"❌ Error in uplink loop: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(0.01)
    
    async def _downlink_loop(self):
        """
        Loop: Read from AudioAdapter, convert to 8-bit PCM, write to pyVoIP call.
        Using AudioAdapter which worked in the original implementation.
        """
        frame_interval = 0.02  # 20ms
        
        while self.running:
            try:
                loop_start = time.time()
                
                # Get audio from adapter (already at 8kHz PCM16, 320 bytes = 20ms)
                pcm16_data = await self.audio_adapter.get_downlink()
                
                if self.voip_call and pcm16_data and len(pcm16_data) == 320:
                    try:
                        # Convert 16-bit signed PCM to 8-bit unsigned PCM
                        # pyVoIP expects 8-bit unsigned (0-255)
                        # Formula: (sample / 256) + 128
                        samples_16bit = np.frombuffer(pcm16_data, dtype=np.int16)
                        # Convert: -32768 to 32767 -> 0-255
                        samples_8bit = np.clip((samples_16bit // 256) + 128, 0, 255).astype(np.uint8)
                        audio_8bit = samples_8bit.tobytes()
                        
                        # Verify frame size (should be 160 bytes for 20ms at 8kHz)
                        if len(audio_8bit) != 160:
                            print(f"⚠️  Unexpected 8-bit PCM frame size: {len(audio_8bit)} bytes (expected 160)")
                            # Pad or truncate to correct size
                            if len(audio_8bit) < 160:
                                audio_8bit = audio_8bit + b'\x80' * (160 - len(audio_8bit))  # 0x80 = silence
                            else:
                                audio_8bit = audio_8bit[:160]
                        
                        # Write to pyVoIP call in executor (synchronous call)
                        await asyncio.get_event_loop().run_in_executor(
                            None,
                            lambda: self.voip_call.write_audio(audio_8bit)
                        )
                    except Exception as e:
                        print(f"⚠️  Error converting/writing audio: {e}")
                        import traceback
                        traceback.print_exc()
                
                # Maintain precise 20ms frame timing
                elapsed = time.time() - loop_start
                sleep_time = max(0, frame_interval - elapsed)
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"❌ Error in downlink loop: {e}")
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
        """Handle audio received from OpenAI - send to AudioAdapter."""
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
        
        # Send to audio adapter for downlink (will resample to 8kHz)
        await self.audio_adapter.send_downlink(audio_data)
    
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

