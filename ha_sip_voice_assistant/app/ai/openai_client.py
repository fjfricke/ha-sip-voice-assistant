"""OpenAI Realtime API client."""
import asyncio
import json
import base64
import websockets
from typing import Optional, Callable, Dict, Any, List
from app.config import Config


class OpenAIRealtimeClient:
    """Client for OpenAI Realtime API WebSocket connection."""
    
    def __init__(
        self,
        config: Config,
        instructions: str,
        tools: List[Dict[str, Any]],
        on_audio_received: Optional[Callable[[bytes], Any]] = None,
        on_tool_call: Optional[Callable[[Dict[str, Any]], Any]] = None,
        on_transcription: Optional[Callable[[str], None]] = None,
    ):
        openai_config = config.get_openai_config()
        self.api_key = openai_config["api_key"]
        self.model = openai_config["model"]
        
        self.instructions = instructions
        self.tools = tools
        self.on_audio_received = on_audio_received
        self.on_tool_call = on_tool_call
        self.on_transcription = on_transcription
        
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.running = False
        self.session_id: Optional[str] = None
        self.is_speaking = False  # Track if OpenAI is currently speaking
    
    async def connect(self):
        """Connect to OpenAI Realtime API."""
        url = f"wss://api.openai.com/v1/realtime?model={self.model}"
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "OpenAI-Beta": "realtime=v1",
        }
        
        self.ws = await websockets.connect(url, extra_headers=headers)
        self.running = True
        
        # Start receiving loop first (to receive session.created)
        asyncio.create_task(self._receive_loop())
        
        # Wait a bit for session to be created before configuring
        await asyncio.sleep(0.5)
        
        # Configure session
        await self._configure_session()
    
    async def disconnect(self):
        """Disconnect from OpenAI Realtime API."""
        self.running = False
        if self.ws:
            await self.ws.close()
    
    async def _configure_session(self):
        """Configure the Realtime API session."""
        # Create session config
        config = {
            "type": "session.update",
            "session": {
                "instructions": self.instructions,
                "voice": "coral",
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "turn_detection": {
                    "type": "server_vad",  # Let OpenAI handle turn detection automatically
                    # "threshold": 0.2,  # Very low threshold for ultra-sensitive voice detection (allows interruptions)
                    # "prefix_padding_ms": 100,  # Minimal padding for immediate response
                    # "silence_duration_ms": 200,  # Very short silence for fast turn-taking and interruptions
                },
                # "interrupt_response": True,
                "modalities": ["text", "audio"],  # Enable both text and audio
                "tools": self.tools,
            },
        }
        
        # Debug: Log session configuration
        print(f"🔧 Configuring OpenAI session with {len(self.tools)} tools")
        print(f"   Instructions length: {len(self.instructions)} chars")
        for i, tool in enumerate(self.tools):
            print(f"   Tool {i+1}: {tool.get('name', 'unknown')} - {tool.get('description', '')[:50]}...")
        
        # Validate JSON before sending
        try:
            json_str = json.dumps(config)
            print(f"🔧 Session config JSON length: {len(json_str)} chars")
        except Exception as e:
            print(f"❌ Failed to serialize session config: {e}")
            raise
        
        await self._send_message(config)
    
    async def send_audio(self, audio_data: bytes):
        """Send audio data to OpenAI."""
        if not self.ws or not self.running:
            return
        
        # Convert PCM16 to base64 (OpenAI expects base64 encoded audio)
        audio_b64 = base64.b64encode(audio_data).decode('utf-8')
        
        message = {
            "type": "input_audio_buffer.append",
            "audio": audio_b64,
        }
        
        await self._send_message(message)
    
    async def _send_message(self, message: Dict[str, Any]):
        """Send a message to OpenAI."""
        if self.ws:
            await self.ws.send(json.dumps(message))
    
    async def _receive_loop(self):
        """Receive loop for OpenAI messages."""
        while self.running and self.ws:
            try:
                message_str = await self.ws.recv()
                message = json.loads(message_str)
                
                await self._handle_message(message)
            except websockets.exceptions.ConnectionClosed:
                print("OpenAI WebSocket connection closed")
                break
            except Exception as e:
                print(f"Error in receive loop: {e}")
                await asyncio.sleep(0.1)
    
    async def _handle_message(self, message: Dict[str, Any]):
        """Handle incoming message from OpenAI."""
        msg_type = message.get("type")
        
        # Only log tool-call related events, not all OpenAI messages
        if msg_type == "session.created":
            self.session_id = message.get("session", {}).get("id")
            print(f"✅ OpenAI session created: {self.session_id}")
        
        elif msg_type == "session.updated":
            print("✅ OpenAI session updated successfully")
        
        elif msg_type == "error":
            error = message.get("error", {})
            error_type = error.get("type", "unknown")
            error_message = error.get("message", "No message")
            print(f"❌ OpenAI error: {error_type} - {error_message}")
            # Print full error for debugging
            print(f"   Full error: {message}")
        
        elif msg_type == "response.created":
            self.is_speaking = True
        
        elif msg_type == "response.done":
            self.is_speaking = False
        
        elif msg_type == "response.interrupted":
            self.is_speaking = False
        
        elif msg_type == "conversation.item.input_audio_buffer.speech_started":
            # User started speaking - AI should stop
            pass
        
        elif msg_type == "conversation.item.input_audio_buffer.speech_stopped":
            # User stopped speaking
            pass
        
        elif msg_type == "response.audio.delta":
            # Audio data chunk received - no logging, just handle it
            audio_b64 = message.get("delta", "")
            if audio_b64 and self.on_audio_received:
                try:
                    audio_data = base64.b64decode(audio_b64)
                    # Callback might be async, so check and await if needed
                    result = self.on_audio_received(audio_data)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    print(f"❌ Error decoding audio: {e}")
                    import traceback
                    traceback.print_exc()
        
        elif msg_type == "response.function_call_arguments.done":
            # Function call arguments completed
            call_id = message.get("call_id")
            arguments_str = message.get("arguments", "{}")
            function_name = message.get("name")  # Name might be in the message directly
            
            print(f"🔧 Tool call: {function_name} (call_id={call_id})")
            
            # Store pending calls and arguments
            if not hasattr(self, '_pending_calls'):
                self._pending_calls = {}
            
            try:
                arguments = json.loads(arguments_str) if arguments_str else {}
                
                # Store with function name if we have it
                if function_name:
                    self._pending_calls[call_id] = {"name": function_name, "arguments": arguments}
                else:
                    self._pending_calls[call_id] = {"arguments": arguments}
                
                # If we have function_name and arguments are complete, trigger tool call now
                # (don't wait for response.function_call.done which might not come)
                if function_name and self.on_tool_call and call_id:
                    await self.on_tool_call({
                        "call_id": call_id,
                        "name": function_name,
                        "arguments": arguments,
                    })
            except json.JSONDecodeError as e:
                print(f"❌ Error parsing function call arguments: {e}")
                print(f"   Raw arguments string: {arguments_str}")
        
        elif msg_type == "response.function_call_arguments.delta":
            # Accumulating function call arguments - handled by done event
            pass
        
        elif msg_type == "response.function_call.done":
            # Function call is complete
            # Try multiple ways to get function_name and call_id
            function_call = message.get("function_call", {})
            call_id = message.get("call_id")
            function_name = function_call.get("name") or message.get("name")
            
            # Get arguments from pending calls
            arguments = {}
            stored_name = None
            if hasattr(self, '_pending_calls') and call_id in self._pending_calls:
                pending = self._pending_calls[call_id]
                arguments = pending.get("arguments", {})
                stored_name = pending.get("name")
                del self._pending_calls[call_id]
            
            # Use stored name if we don't have one from message
            if not function_name and stored_name:
                function_name = stored_name
            
            # Only trigger if we haven't already (from function_call_arguments.done)
            if self.on_tool_call and call_id and function_name:
                await self.on_tool_call({
                    "call_id": call_id,
                    "name": function_name,
                    "arguments": arguments,
                })
        
        elif msg_type == "response.output_item.added":
            # New output item added to response - only handle function_call items
            item = message.get("item", {})
            item_type = item.get("type")
            
            # Check if this is a function_call output item
            if item_type == "function_call":
                call_id = item.get("call_id")
                function_name = item.get("name")  # Name is directly on item, not in function_call
                arguments_str = item.get("arguments", "{}")
                
                # Parse arguments if it's a string
                if isinstance(arguments_str, str):
                    try:
                        arguments = json.loads(arguments_str) if arguments_str else {}
                    except json.JSONDecodeError:
                        arguments = {}
                else:
                    arguments = arguments_str or {}
                
                # Wait for response.function_call_arguments.done if arguments are not complete yet
                # If arguments are empty or incomplete, wait for the done event
                if not arguments or arguments_str == "":
                    # Don't trigger yet - wait for response.function_call_arguments.done instead
                    pass
                else:
                    # Arguments are ready - trigger tool call handler
                    if self.on_tool_call and call_id and function_name:
                        await self.on_tool_call({
                            "call_id": call_id,
                            "name": function_name,
                            "arguments": arguments,
                        })
    
    async def request_response(self):
        """Request a response from OpenAI (after sending audio)."""
        message = {
            "type": "response.create",
        }
        await self._send_message(message)
    
    async def submit_tool_output(self, tool_call_id: str, output: Any):
        """Submit tool execution result back to OpenAI."""
        # Format output for OpenAI - must be a JSON string
        if isinstance(output, dict):
            output_str = json.dumps(output)
        elif isinstance(output, list):
            output_str = json.dumps(output)
        else:
            output_str = json.dumps({"result": str(output)})

        print(f"🔧 Tool output submitted for call_id={tool_call_id}")

        # Use conversation.item.create with function_call_output type
        # This is the correct way for Realtime API
        message = {
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "call_id": tool_call_id,
                "output": output_str,
            },
        }

        await self._send_message(message)

        # After submitting output, request a new response so OpenAI can speak about the result
        await asyncio.sleep(0.3)  # Small delay to ensure message is processed
        await self.request_response()

