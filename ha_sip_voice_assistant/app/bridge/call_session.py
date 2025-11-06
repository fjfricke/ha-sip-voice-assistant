"""Call session management."""
import asyncio
import time
import threading
import concurrent.futures
from typing import Dict, Any, Optional
from app.config import Config
from app.bridge.audio_adapter import AudioAdapter
from app.ai.openai_client import OpenAIRealtimeClient
from app.ai.tool_handler import ToolHandler
from app.homeassistant.client import HomeAssistantClient
from app.utils.pin_verification import PINVerifier
from app.utils.caller_mapping import get_caller_settings

# Try to import PJSIP call object
try:
    import pjsua2 as pj
    PJSIP_AVAILABLE = True
except ImportError:
    PJSIP_AVAILABLE = False
    pj = None


class PJSIPThreadPool:
    """Thread pool with PJSIP-registered threads."""
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._executor = None
        self._ep = None
    
    def initialize(self, ep, max_workers=2):
        """Initialize the thread pool with PJSIP endpoint."""
        self._ep = ep
        
        def worker_init():
            """Initialize worker thread - register with PJSIP."""
            try:
                if self._ep:
                    self._ep.libRegisterThread("PJSIPWorker")
            except Exception:
                pass  # Thread might already be registered
        
        # Create executor with custom initializer
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="PJSIPWorker",
            initializer=worker_init
        )
    
    def submit(self, fn, *args, **kwargs):
        """Submit a task to the thread pool."""
        if self._executor is None:
            raise RuntimeError("Thread pool not initialized")
        return self._executor.submit(fn, *args, **kwargs)
    
    def shutdown(self, wait=True):
        """Shutdown the thread pool."""
        if self._executor:
            self._executor.shutdown(wait=wait)


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
        self.pjsip_call = call_info.get("pjsip_call") if PJSIP_AVAILABLE else None
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
        
        # PJSIP thread pool (shared singleton)
        self._pjsip_executor = None
        if PJSIP_AVAILABLE and self.pjsip_call:
            # Get endpoint from adapter
            if hasattr(self.pjsip_call, 'adapter') and hasattr(self.pjsip_call.adapter, 'ep'):
                if not hasattr(CallSession, '_pjsip_pool_initialized'):
                    CallSession._pjsip_pool = PJSIPThreadPool()
                    CallSession._pjsip_pool.initialize(self.pjsip_call.adapter.ep, max_workers=2)
                    CallSession._pjsip_pool_initialized = True
                self._pjsip_executor = CallSession._pjsip_pool
    
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
        
        # Answer the call FIRST if using PJSIP (before triggering OpenAI response)
        # Must be done in a registered thread
        if self.pjsip_call:
            try:
                def _answer_call():
                    """Answer call in PJSIP-registered thread."""
                    try:
                        if self._pjsip_executor and self._pjsip_executor._ep:
                            # Register this thread if needed
                            if not self._pjsip_executor._ep.libIsThreadRegistered():
                                self._pjsip_executor._ep.libRegisterThread("AnswerCall")
                        call_param = pj.CallOpParam()
                        call_param.statusCode = pj.PJSIP_SC_OK
                        self.pjsip_call.answer(call_param)
                        return True
                    except Exception as e:
                        print(f"❌ Error in _answer_call: {e}")
                        return False
                
                # Use executor if available, otherwise call directly (but this might fail)
                if self._pjsip_executor and self._pjsip_executor._executor:
                    result = await asyncio.get_event_loop().run_in_executor(
                        self._pjsip_executor._executor,
                        _answer_call
                    )
                    if result:
                        print("✅ Answered PJSIP call")
                        
                        # Wait for audio bridge to be ready (audio_running flag)
                        # This ensures audio frames won't be lost
                        max_wait = 5.0  # Maximum wait time in seconds
                        wait_interval = 0.1  # Check every 100ms
                        waited = 0.0
                        while waited < max_wait:
                            if hasattr(self.pjsip_call, 'audio_running') and self.pjsip_call.audio_running:
                                break
                            await asyncio.sleep(wait_interval)
                            waited += wait_interval
                        
                        if waited >= max_wait:
                            print("⚠️  Audio bridge not ready after waiting, continuing anyway")
                        else:
                            print("✅ Audio bridge ready")
                else:
                    # Fallback: try direct call (might fail if not in registered thread)
                    call_param = pj.CallOpParam()
                    call_param.statusCode = pj.PJSIP_SC_OK
                    self.pjsip_call.answer(call_param)
                    print("✅ Answered PJSIP call")
                    
                    # Wait for audio bridge
                    max_wait = 5.0
                    wait_interval = 0.1
                    waited = 0.0
                    while waited < max_wait:
                        if hasattr(self.pjsip_call, 'audio_running') and self.pjsip_call.audio_running:
                            break
                        await asyncio.sleep(wait_interval)
                        waited += wait_interval
                    
                    if waited < max_wait:
                        print("✅ Audio bridge ready")
            except Exception as e:
                print(f"❌ Error answering call: {e}")
                import traceback
                traceback.print_exc()
        
        # Trigger OpenAI to start the conversation (instructions will guide the welcome message)
        # Now that call is answered and audio bridge is ready, we can safely request response
        await asyncio.sleep(0.2)  # Small delay for session to be ready
        await self.ai_client.request_response()
        
        # Start audio streaming tasks
        # Uplink: PJSIP call -> AudioAdapter -> OpenAI
        self.uplink_task = asyncio.create_task(self._uplink_loop())
        # Downlink: OpenAI -> AudioAdapter -> PJSIP call
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
        
        # Hangup call if using PJSIP
        # Must be done in a registered thread
        if self.pjsip_call:
            try:
                def _hangup_call():
                    """Hangup call in PJSIP-registered thread."""
                    try:
                        if self._pjsip_executor and self._pjsip_executor._ep:
                            # Register this thread if needed
                            if not self._pjsip_executor._ep.libIsThreadRegistered():
                                self._pjsip_executor._ep.libRegisterThread("HangupCall")
                        call_param = pj.CallOpParam()
                        call_param.statusCode = pj.PJSIP_SC_DECLINE
                        self.pjsip_call.hangup(call_param)
                        return True
                    except Exception as e:
                        print(f"⚠️  Error in _hangup_call: {e}")
                        return False
                
                # Use executor if available
                if self._pjsip_executor and self._pjsip_executor._executor:
                    await asyncio.get_event_loop().run_in_executor(
                        self._pjsip_executor._executor,
                        _hangup_call
                    )
                    print("✅ Hung up PJSIP call")
                else:
                    # Fallback: try direct call
                    call_param = pj.CallOpParam()
                    call_param.statusCode = pj.PJSIP_SC_DECLINE
                    self.pjsip_call.hangup(call_param)
                    print("✅ Hung up PJSIP call")
            except Exception as e:
                print(f"⚠️  Error hanging up call: {e}")
                import traceback
                traceback.print_exc()
        
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
        Loop: Read from PJSIP call, convert via AudioAdapter, send to OpenAI.
        Using AudioAdapter which worked in the original implementation.
        """
        frame_interval = 0.02  # 20ms
        
        while self.running:
            try:
                loop_start = time.time()
                
                # Read audio from PJSIP call
                # PJSIP provides PCM16 at 8kHz (320 bytes = 20ms)
                if self.pjsip_call:
                    try:
                        # Direct queue access - queue operations are thread-safe and don't call PJSIP
                        audio_pcm16 = self.pjsip_call.get_audio_frame(blocking=False)
                        
                        if audio_pcm16 and len(audio_pcm16) == 320:
                            # PJSIP already provides PCM16, so send directly to adapter
                            await self.audio_adapter.send_uplink(audio_pcm16)
                        else:
                            # Send silence if no audio
                            await self.audio_adapter.send_uplink(b'\x00' * 320)
                    except Exception as e:
                        # Call might have ended
                        print(f"⚠️  Error reading audio from call: {e}")
                        self.running = False
                        break
                else:
                    # No pjsip_call, send silence
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
        Loop: Read from AudioAdapter, write PCM16 to PJSIP call.
        Using AudioAdapter which worked in the original implementation.
        """
        frame_interval = 0.02  # 20ms
        
        while self.running:
            try:
                loop_start = time.time()
                
                # Get audio from adapter (already at 8kHz PCM16, 320 bytes = 20ms)
                pcm16_data = await self.audio_adapter.get_downlink()
                
                if self.pjsip_call and pcm16_data and len(pcm16_data) == 320:
                    try:
                        # Direct queue access - queue operations are thread-safe and don't call PJSIP
                        # PJSIP expects PCM16, so we can send directly
                        self.pjsip_call.put_audio_frame(pcm16_data)
                    except Exception as e:
                        print(f"⚠️  Error writing audio: {e}")
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

