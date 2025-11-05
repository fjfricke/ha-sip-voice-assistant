"""Adapter for pyVoIP library to provide async-compatible interface."""
import asyncio
import threading
import socket
import re
from typing import Optional, Callable, Awaitable, Dict, Any
from pyVoIP.VoIP import VoIPPhone, VoIPCall, InvalidStateError


class PyVoIPAdapter:
    """Async-compatible adapter for pyVoIP's VoIPPhone."""
    
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
        
        # pyVoIP phone instance
        self.phone: Optional[VoIPPhone] = None
        
        # Running state
        self.running = False
        self.registered = False
        
        # Active calls tracking
        self.active_calls: Dict[str, Dict[str, Any]] = {}
        
        # Thread for pyVoIP (it runs in a separate thread)
        self.phone_thread: Optional[threading.Thread] = None
        self.phone_lock = threading.Lock()
        
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
    
    def _get_header_value(self, headers: dict, key: str, default: str = "") -> str:
        """Get header value, handling string, list, and dict formats."""
        value = headers.get(key, default)
        if isinstance(value, list):
            return str(value[0]) if value else default
        elif isinstance(value, dict):
            # If it's a dict, try to get a string representation or a specific key
            return str(value) if value else default
        return str(value) if value else default
    
    def _call_callback(self, call: VoIPCall):
        """Synchronous callback from pyVoIP - bridge to async."""
        # Get call_id (pyVoIP uses snake_case)
        call_id_str = call.call_id
        
        # Get remote info from request headers
        request = call.request
        from_header = self._get_header_value(request.headers, "From", "")
        
        # Try to get remote IP from Via header (most reliable)
        remote_ip = self.server  # Default to server IP
        remote_port = 5060
        
        via_header = self._get_header_value(request.headers, "Via", "")
        
        if via_header:
            # Via: SIP/2.0/UDP 192.168.1.1:5060;branch=...
            # Extract IP and port from Via header
            match = re.search(r'(\d+\.\d+\.\d+\.\d+)(?::(\d+))?', str(via_header))
            if match:
                remote_ip = match.group(1)
                if match.group(2):
                    remote_port = int(match.group(2))
        
        # Extract caller number from From header (format: "Name" <sip:number@domain> or sip:number@domain)
        caller_id = "unknown"
        if from_header:
            # Try to extract number from From header
            match = re.search(r'sip:([^@]+)@', from_header)
            if match:
                caller_id = match.group(1)
            else:
                # Fallback: try to extract from quoted name
                match = re.search(r'["\']([^"\']+)["\']', from_header)
                if match:
                    caller_id = match.group(1)
        
        # Use call_id directly (it's already unique)
        call_id = call_id_str
        
        print(f"📞 Incoming call from pyVoIP: {caller_id} (Call-ID: {call_id})")
        
        # Create call info dictionary matching our current interface
        call_info = {
            "call_id": call_id,
            "caller_id": caller_id,
            "from_header": from_header,
            "to_header": self._get_header_value(request.headers, "To", f"<sip:{self.username}@{self.server}>"),
            "remote_ip": remote_ip,
            "remote_port": remote_port,
            "rtp_info": {},  # pyVoIP handles RTP internally
            "voip_call": call,  # Store the pyVoIP call object
        }
        
        # Add to active calls
        self.active_calls[call_id] = call_info
        
        # Bridge to async handler
        if self.on_incoming_call and self.loop:
            try:
                # Schedule async callback in the event loop
                asyncio.run_coroutine_threadsafe(
                    self.on_incoming_call(caller_id, call_info),
                    self.loop
                )
            except Exception as e:
                print(f"❌ Error calling async callback: {e}")
                import traceback
                traceback.print_exc()
    
    async def start(self):
        """Start the pyVoIP phone (async wrapper)."""
        if self.running:
            return
        
        self.loop = asyncio.get_running_loop()
        self.running = True
        
        # Create pyVoIP phone
        # Note: pyVoIP uses threading, so we'll run it in a separate thread
        def start_phone():
            try:
                self.phone = VoIPPhone(
                    server=self.server,
                    port=self.server_port,
                    username=self.username,
                    password=self.password,
                    myIP=self.local_ip,
                    callCallback=self._call_callback,
                    sipPort=self.local_port,
                    rtpPortLow=10000,
                    rtpPortHigh=20000,
                )
                
                print(f"Starting pyVoIP phone...")
                print(f"  Server: {self.server}:{self.server_port}")
                print(f"  Username: {self.username}")
                print(f"  Local IP: {self.local_ip}")
                print(f"  SIP Port: {self.local_port}")
                
                self.phone.start()
                self.registered = True
                print("✅ pyVoIP phone started and registered")
            except Exception as e:
                print(f"❌ Error starting pyVoIP phone: {e}")
                import traceback
                traceback.print_exc()
                self.registered = False
        
        # Start phone in a separate thread
        self.phone_thread = threading.Thread(target=start_phone, daemon=True)
        self.phone_thread.start()
        
        # Wait a bit for registration
        await asyncio.sleep(2)
        
        if self.registered:
            print("✅ pyVoIP registration confirmed")
        else:
            print("⚠️  pyVoIP registration status: waiting...")
    
    async def stop(self):
        """Stop the pyVoIP phone (async wrapper)."""
        if not self.running:
            return
        
        self.running = False
        
        # Stop phone in thread-safe manner
        if self.phone:
            def stop_phone():
                try:
                    self.phone.stop()
                    print("✅ pyVoIP phone stopped")
                except Exception as e:
                    print(f"⚠️  Error stopping pyVoIP phone: {e}")
            
            # Run stop in executor to avoid blocking
            await asyncio.get_event_loop().run_in_executor(None, stop_phone)
        
        # Wait for thread to finish
        if self.phone_thread:
            self.phone_thread.join(timeout=5)
        
        self.registered = False
        self.active_calls.clear()
    
    def get_call_info(self, call_id: str) -> Optional[Dict[str, Any]]:
        """Get information about an active call."""
        return self.active_calls.get(call_id)
    
    def _schedule_refresh_after_call(self):
        """Schedule registration refresh after call ends (no-op for pyVoIP)."""
        # pyVoIP handles registration internally, so this is a no-op
        pass

