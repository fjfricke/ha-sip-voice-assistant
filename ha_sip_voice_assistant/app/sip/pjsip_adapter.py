"""Adapter for PJSIP (pjsua2) library to provide async-compatible interface."""
import asyncio
import threading
import socket
import re
import queue
from typing import Optional, Callable, Awaitable, Dict, Any

try:
    import pjsua2 as pj
    PJSIP_AVAILABLE = True
except ImportError:
    PJSIP_AVAILABLE = False
    pj = None


# Note: AudioMediaPort implementation will need to be completed based on actual PJSIP API
# The exact API depends on pjsua2 version and how AudioMediaPort is exposed
# For now, we'll use a queue-based approach that can be connected to proper ports later


class QueueAudioPort(pj.AudioMediaPort):
    """Custom AudioMediaPort that bridges audio to/from Python queues."""
    
    def __init__(self, rx_queue, tx_queue, sample_rate=8000, frame_size_ms=20):
        """Initialize port with queues.
        
        Args:
            rx_queue: Queue for receiving audio (from call)
            tx_queue: Queue for transmitting audio (to call)
            sample_rate: Sample rate in Hz (default 8000)
            frame_size_ms: Frame size in milliseconds (default 20)
        """
        pj.AudioMediaPort.__init__(self)
        self.rx_queue = rx_queue
        self.tx_queue = tx_queue
        self.sample_rate = sample_rate
        self.frame_size_ms = frame_size_ms
        self.frame_size_samples = (sample_rate * frame_size_ms) // 1000
        self.frame_size_bytes = self.frame_size_samples * 2  # PCM16 = 2 bytes per sample
        
        # Create audio format
        fmt = pj.MediaFormatAudio()
        fmt.type = pj.PJMEDIA_TYPE_AUDIO
        fmt.clockRate = sample_rate
        fmt.channelCount = 1
        fmt.bitsPerSample = 16
        fmt.frameTimeUsec = frame_size_ms * 1000  # microseconds
        
        # Create port
        self.createPort("QueueAudioPort", fmt)
    
    def onFrameReceived(self, frame):
        """Called by PJSIP when a frame is received (RX - uplink from call)."""
        try:
            # Check if frame is audio type
            if hasattr(frame, 'type') and frame.type == pj.PJMEDIA_FRAME_TYPE_AUDIO:
                # Get audio data from frame
                if hasattr(frame, 'buf') and frame.buf:
                    # Convert ByteVector to bytes
                    try:
                        # ByteVector can be converted to bytes directly
                        audio_data = bytes(frame.buf) if hasattr(frame.buf, '__iter__') else frame.buf
                        if audio_data:
                            # Put audio in queue (non-blocking)
                            try:
                                self.rx_queue.put_nowait(audio_data)
                            except queue.Full:
                                pass  # Skip if queue is full
                    except Exception:
                        # Silently ignore conversion errors
                        pass
        except Exception:
            # Silently ignore errors to avoid breaking audio stream
            pass
    
    def onFrameRequested(self, frame):
        """Called by PJSIP when a frame is requested (TX - downlink to call)."""
        try:
            # Set frame type
            if hasattr(frame, 'type'):
                frame.type = pj.PJMEDIA_FRAME_TYPE_AUDIO
            
            # Get audio from queue (non-blocking)
            try:
                audio_data = self.tx_queue.get_nowait()
                if audio_data and len(audio_data) >= self.frame_size_bytes:
                    # Fill frame buffer (ByteVector)
                    audio_bytes = audio_data[:self.frame_size_bytes]
                    if hasattr(frame, 'buf'):
                        # Use assign_from_bytes if available, otherwise use append
                        if hasattr(frame.buf, 'assign_from_bytes'):
                            frame.buf.assign_from_bytes(audio_bytes)
                        else:
                            frame.buf.clear()
                            for byte_val in audio_bytes:
                                frame.buf.append(byte_val)
                    # Set frame size
                    if hasattr(frame, 'size'):
                        frame.size = len(audio_bytes)
                    return
            except queue.Empty:
                pass
            
            # Return silence if queue is empty
            silence = b'\x00' * self.frame_size_bytes
            if hasattr(frame, 'buf'):
                if hasattr(frame.buf, 'assign_from_bytes'):
                    frame.buf.assign_from_bytes(silence)
                else:
                    frame.buf.clear()
                    for byte_val in silence:
                        frame.buf.append(byte_val)
            if hasattr(frame, 'size'):
                frame.size = len(silence)
        except Exception:
            # Return silence on error
            silence = b'\x00' * self.frame_size_bytes
            if hasattr(frame, 'buf'):
                if hasattr(frame.buf, 'assign_from_bytes'):
                    frame.buf.assign_from_bytes(silence)
                else:
                    frame.buf.clear()
                    for byte_val in silence:
                        frame.buf.append(byte_val)
            if hasattr(frame, 'size'):
                frame.size = len(silence)


class PJSIPAccount(pj.Account):
    """PJSIP Account class to handle registration and incoming calls."""
    
    def __init__(self, adapter):
        pj.Account.__init__(self)
        self.adapter = adapter
    
    def onIncomingCall(self, prm):
        """Handle incoming call."""
        call = PJSIPCall(self.adapter, self, prm.callId)
        call_info = call.getInfo()
        
        # Extract caller information
        remote_uri = call_info.remoteUri
        caller_id = "unknown"
        
        # Parse URI: sip:number@domain or "Name" <sip:number@domain>
        if remote_uri:
            match = re.search(r'sip:([^@]+)@', remote_uri)
            if match:
                caller_id = match.group(1)
            else:
                # Try to extract from display name
                match = re.search(r'["\']([^"\']+)["\']', remote_uri)
                if match:
                    caller_id = match.group(1)
        
        call_id = call_info.callIdString
        
        print(f"📞 Incoming call from PJSIP: {caller_id} (Call-ID: {call_id})")
        
        # Create call info dictionary
        call_info_dict = {
            "call_id": call_id,
            "caller_id": caller_id,
            "from_header": remote_uri,
            "to_header": call_info.localUri,
            "remote_ip": "",  # Will be extracted from RTP if needed
            "remote_port": 0,
            "rtp_info": {},
            "pjsip_call": call,  # Store the PJSIP call object
            "sample_rate": 8000,  # Default, will be updated from media info
        }
        
        # Get media info to determine sample rate
        try:
            media_info = call.getStreamInfo(0)  # Get first audio stream
            if media_info and hasattr(media_info, 'codecName'):
                # PJSIP typically uses 8kHz for G.711
                call_info_dict["sample_rate"] = 8000
        except Exception as e:
            print(f"⚠️  Could not get media info: {e}")
        
        # Add to active calls
        self.adapter.active_calls[call_id] = call_info_dict
        
        # Bridge to async handler
        if self.adapter.on_incoming_call and self.adapter.loop:
            try:
                asyncio.run_coroutine_threadsafe(
                    self.adapter.on_incoming_call(caller_id, call_info_dict),
                    self.adapter.loop
                )
            except Exception as e:
                print(f"❌ Error calling async callback: {e}")
                import traceback
                traceback.print_exc()


class PJSIPCall(pj.Call):
    """PJSIP Call class to handle call state and audio."""
    
    def __init__(self, adapter, account, call_id=-1):
        pj.Call.__init__(self, account, call_id)
        self.adapter = adapter
        # Audio queues for bridging to async
        self.audio_rx_queue = queue.Queue(maxsize=10)  # Incoming audio from call
        self.audio_tx_queue = queue.Queue(maxsize=10)  # Outgoing audio to call
        self.audio_running = False
        self.audio_port = None  # Will be set when audio media is active
        self.queue_port = None  # Custom AudioMediaPort for bridging
    
    def onCallState(self, prm):
        """Handle call state changes.
        
        Args:
            prm: CallStateParam from PJSIP
        """
        ci = self.getInfo()
        print(f"📞 Call state changed: {ci.stateText} (code: {ci.lastStatusCode})")
        
        if ci.state == pj.PJSIP_INV_STATE_DISCONNECTED:
            call_id = ci.callIdString
            self.audio_running = False
            
            # Disconnect audio bridge
            if self.queue_port and self.audio_port:
                try:
                    self.audio_port.stopTransmit(self.queue_port)
                    self.queue_port.stopTransmit(self.audio_port)
                except Exception:
                    pass
            
            if call_id in self.adapter.active_calls:
                print(f"📞 Call {call_id} disconnected")
    
    def onCallMediaState(self, prm):
        """Handle media state changes.
        
        Args:
            prm: OnCallMediaStateParam from PJSIP
        """
        ci = self.getInfo()
        for mi in ci.media:
            if mi.type == pj.PJMEDIA_TYPE_AUDIO:
                if mi.status == pj.PJSUA_CALL_MEDIA_ACTIVE:
                    # Audio is active - connect to conference bridge for audio routing
                    try:
                        # Get endpoint for audio device manager
                        ep = self.adapter.ep if self.adapter else None
                        if not ep:
                            print("⚠️  No endpoint available for audio routing")
                            return
                        
                        # Get audio media for this call
                        aud_med = self.getAudioMedia(-1)  # Get first audio media
                        if not aud_med:
                            print("⚠️  Could not get audio media")
                            return
                        
                        self.audio_running = True
                        self.audio_port = aud_med  # Store for audio bridge
                        print(f"✅ Audio stream active for call {ci.callIdString}")
                        
                        # Create custom AudioMediaPort for bridging
                        try:
                            self.queue_port = QueueAudioPort(
                                self.audio_rx_queue,
                                self.audio_tx_queue,
                                sample_rate=8000,
                                frame_size_ms=20
                            )
                            
                            # Connect the queue port to the call's audio media
                            # This creates a bidirectional bridge:
                            # - Call -> Queue (RX): Audio from call goes to queue_port via onFrameReceived
                            # - Queue -> Call (TX): Audio from queue goes to call via onFrameRequested
                            aud_med.startTransmit(self.queue_port)  # Call -> Queue (RX)
                            self.queue_port.startTransmit(aud_med)  # Queue -> Call (TX)
                            
                            print(f"🎵 Audio bridge connected (RX: call->queue, TX: queue->call)")
                        except Exception as e:
                            print(f"⚠️  Error creating audio bridge port: {e}")
                            import traceback
                            traceback.print_exc()
                    except Exception as e:
                        print(f"⚠️  Error setting up audio media: {e}")
                        import traceback
                        traceback.print_exc()
                else:
                    print(f"⚠️  Audio stream status: {mi.status} for call {ci.callIdString}")
    
    def get_audio_frame(self, blocking=False):
        """Get audio frame from call (for uplink)."""
        try:
            if blocking:
                return self.audio_rx_queue.get(timeout=0.02)
            else:
                return self.audio_rx_queue.get_nowait()
        except:
            return None
    
    def put_audio_frame(self, audio_data):
        """Put audio frame to call (for downlink)."""
        try:
            self.audio_tx_queue.put_nowait(audio_data)
        except:
            pass


class PJSIPAdapter:
    """Async-compatible adapter for PJSIP's pjsua2."""
    
    def __init__(
        self,
        server: str,
        username: str,
        password: str,
        display_name: str = "HA Voice Assistant",
        transport: str = "udp",
        port: int = 5060,
        bind_port: Optional[int] = None,
        on_incoming_call: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None,
    ):
        if not PJSIP_AVAILABLE:
            raise ImportError("pjsua2 is not available. Please install PJSIP.")
        
        self.server = server
        self.username = username
        self.password = password
        self.display_name = display_name
        self.transport = transport
        self.server_port = port
        self.local_port = bind_port if bind_port is not None else port
        self.on_incoming_call = on_incoming_call
        
        # Get local IP
        self.local_ip = self._get_local_ip()
        
        # PJSIP components
        self.ep: Optional[pj.Endpoint] = None
        self.account: Optional[PJSIPAccount] = None
        
        # Running state
        self.running = False
        self.registered = False
        
        # Active calls tracking
        self.active_calls: Dict[str, Dict[str, Any]] = {}
        
        # Thread for PJSIP (it runs in a separate thread)
        self.pjsip_thread: Optional[threading.Thread] = None
        self.pjsip_lock = threading.Lock()
        
        # Event loop reference for callbacks
        self.loop: Optional[asyncio.AbstractEventLoop] = None
    
    def _get_local_ip(self) -> str:
        """Get local IP address."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"
    
    def _run_pjsip(self):
        """Run PJSIP endpoint in a separate thread."""
        try:
            # Create endpoint first
            self.ep = pj.Endpoint()
            self.ep.libCreate()
            
            # Configure endpoint
            ep_cfg = pj.EpConfig()
            # Disable PJSIP verbose logging - we'll only show our own high-level messages
            # Levels: 0=DISABLE, 1=ERROR, 2=WARNING, 3=INFO, 4=DEBUG, 5=TRACE, 6=VERBOSE
            ep_cfg.logConfig.level = 0  # Disable all PJSIP logs
            ep_cfg.logConfig.consoleLevel = 0  # Disable all PJSIP console logs
            ep_cfg.uaConfig.maxCalls = 10
            ep_cfg.uaConfig.userAgent = self.display_name
            
            # Initialize endpoint (must be done before thread registration)
            self.ep.libInit(ep_cfg)
            
            # Register this thread with PJSIP (after libInit, before other operations)
            # Check if thread is already registered to avoid "re-registering" error
            try:
                if not self.ep.libIsThreadRegistered():
                    self.ep.libRegisterThread("PJSIPMain")
            except Exception as e:
                # Thread might already be registered - this is okay
                print(f"ℹ️  Thread registration: {e}")
            
            # Create UDP transport
            transport_cfg = pj.TransportConfig()
            transport_cfg.port = self.local_port
            self.ep.transportCreate(pj.PJSIP_TRANSPORT_UDP, transport_cfg)
            
            # Start endpoint
            self.ep.libStart()
            
            print(f"Starting PJSIP endpoint...")
            print(f"  Server: {self.server}:{self.server_port}")
            print(f"  Username: {self.username}")
            print(f"  Local IP: {self.local_ip}")
            print(f"  SIP Port: {self.local_port}")
            
            # Create account configuration
            acc_cfg = pj.AccountConfig()
            acc_cfg.idUri = f"sip:{self.username}@{self.server}"
            acc_cfg.regConfig.registrarUri = f"sip:{self.server}:{self.server_port}"
            
            # Add authentication credentials
            auth_cred = pj.AuthCredInfo()
            auth_cred.realm = "*"
            auth_cred.scheme = "digest"
            auth_cred.username = self.username
            auth_cred.dataType = pj.PJSIP_CRED_DATA_PLAIN_PASSWD
            auth_cred.data = self.password
            acc_cfg.sipConfig.authCreds.append(auth_cred)
            
            # Create account
            self.account = PJSIPAccount(self)
            self.account.create(acc_cfg)
            
            # Wait for registration
            import time
            for _ in range(10):  # Wait up to 5 seconds
                time.sleep(0.5)
                acc_info = self.account.getInfo()
                if acc_info.regIsActive:
                    self.registered = True
                    print("✅ PJSIP account registered")
                    break
            
            if not self.registered:
                print("⚠️  PJSIP registration not confirmed yet")
            
            # Keep endpoint running
            while self.running:
                self.ep.libHandleEvents(100)  # Handle events with 100ms timeout
                time.sleep(0.1)
            
        except Exception as e:
            print(f"❌ Error in PJSIP thread: {e}")
            import traceback
            traceback.print_exc()
            self.registered = False
    
    async def start(self):
        """Start the PJSIP endpoint (async wrapper)."""
        if self.running:
            return
        
        self.loop = asyncio.get_running_loop()
        self.running = True
        
        # Start PJSIP in a separate thread
        self.pjsip_thread = threading.Thread(target=self._run_pjsip, daemon=True)
        self.pjsip_thread.start()
        
        # Wait a bit for initialization
        await asyncio.sleep(2)
        
        if self.registered:
            print("✅ PJSIP registration confirmed")
        else:
            print("⚠️  PJSIP registration status: waiting...")
    
    async def stop(self):
        """Stop the PJSIP endpoint (async wrapper)."""
        if not self.running:
            return
        
        self.running = False
        
        # Stop endpoint in thread-safe manner
        if self.ep:
            def stop_endpoint():
                try:
                    # Hangup all calls
                    if self.account:
                        acc_info = self.account.getInfo()
                        for call_id in acc_info.calls:
                            try:
                                call = pj.Call()
                                call = self.account.findCall(call_id)
                                if call:
                                    call.hangup(pj.CallOpParam())
                            except Exception as e:
                                print(f"⚠️  Error hanging up call: {e}")
                    
                    # Destroy endpoint
                    self.ep.libDestroy()
                    print("✅ PJSIP endpoint stopped")
                except Exception as e:
                    print(f"⚠️  Error stopping PJSIP endpoint: {e}")
            
            # Run stop in executor to avoid blocking
            await asyncio.get_event_loop().run_in_executor(None, stop_endpoint)
        
        # Wait for thread to finish
        if self.pjsip_thread:
            self.pjsip_thread.join(timeout=5)
        
        self.registered = False
        self.active_calls.clear()
    
    def get_call_info(self, call_id: str) -> Optional[Dict[str, Any]]:
        """Get information about an active call."""
        return self.active_calls.get(call_id)
    
    def _schedule_refresh_after_call(self):
        """Schedule registration refresh after call ends (no-op for PJSIP)."""
        # PJSIP handles registration refresh internally, so this is a no-op
        pass

