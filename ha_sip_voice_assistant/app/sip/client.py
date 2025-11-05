"""SIP client for registration and call handling."""
import asyncio
import socket
import hashlib
import random
import re
import time
from typing import Optional, Callable, Awaitable, Dict, Any
from urllib.parse import quote, unquote


class SIPClient:
    """SIP client for registration with FritzBox and call handling."""
    
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
        self.server_port = port  # Server port (where FritzBox listens, typically 5060)
        self.on_incoming_call = on_incoming_call
        
        self.local_ip = self._get_local_ip()
        # Bind to bind_port if provided, otherwise use server_port
        # This is the port we actually listen on locally
        self.local_port = bind_port if bind_port is not None else port
        # We advertise our actual bind port in Contact/Via headers
        # FritzBox will send responses to this port
        
        self.transport_obj: Optional[asyncio.DatagramTransport] = None
        self.protocol: Optional[SIPProtocol] = None
        self.running = False
        
        # Registration state
        self.registered = False
        self.call_id_counter = 0
        self.cseq = 1
        self.tag = self._generate_tag()
        self.branch = None
        
        # Connection monitoring state
        self.last_registration_time: Optional[float] = None
        self.registration_expires_at: Optional[float] = None
        self.reconnect_attempts = 0
        self.reconnect_task: Optional[asyncio.Task] = None
        self._registration_refresh_task: Optional[asyncio.Task] = None
        self._options_keepalive_task: Optional[asyncio.Task] = None
        self._options_health_check_task: Optional[asyncio.Task] = None
        
        # Pending request tracking for timeout detection
        self.pending_registrations: Dict[str, Dict[str, Any]] = {}  # call_id -> {timestamp, task}
        self.registration_timeout = 10.0  # 10 seconds timeout for REGISTER responses
        
        # OPTIONS keep-alive health tracking
        self.last_options_response_time: Optional[float] = None
        self.last_options_send_time: Optional[float] = None
        self.options_health_check_interval = 60  # Check health every 60 seconds
        self.options_max_no_response_time = 90  # If no response for 90 seconds, connection is dead
        
        # Digest authentication state
        self.auth_realm = None
        self.auth_nonce = None
        self.auth_opaque = None
        self.registration_call_id = None
        self.auth_retry_count = 0
        
        # Active calls
        self.active_calls: Dict[str, Dict[str, Any]] = {}
    
    def _get_local_ip(self) -> str:
        """Get local IP address."""
        try:
            # Connect to external address to determine local IP
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            return local_ip
        except Exception:
            return "127.0.0.1"
    
    def _generate_tag(self) -> str:
        """Generate a SIP tag."""
        return hashlib.md5(f"{random.random()}{self.username}".encode()).hexdigest()[:8]
    
    def _generate_call_id(self) -> str:
        """Generate a SIP Call-ID."""
        self.call_id_counter += 1
        return hashlib.md5(f"{self.local_ip}{self.local_port}{self.call_id_counter}".encode()).hexdigest()
    
    def _generate_branch(self) -> str:
        """Generate a SIP branch identifier."""
        return f"z9hG4bK{hashlib.md5(f'{random.random()}{time.time()}'.encode()).hexdigest()[:16]}"
    
    async def start(self):
        """Start the SIP client."""
        loop = asyncio.get_event_loop()
        
        print(f"Binding UDP socket to {self.local_ip}:{self.local_port}")
        
        # Create UDP transport - bind to local IP to receive responses
        try:
            self.transport_obj, self.protocol = await loop.create_datagram_endpoint(
                lambda: SIPProtocol(self),
                local_addr=(self.local_ip, self.local_port),
            )
            print(f"✅ UDP socket bound successfully to {self.local_ip}:{self.local_port}")
        except OSError as e:
            print(f"❌ Failed to bind UDP socket: {e}")
            # Try binding to 0.0.0.0 instead
            print(f"Trying to bind to 0.0.0.0:{self.local_port} instead...")
            self.transport_obj, self.protocol = await loop.create_datagram_endpoint(
                lambda: SIPProtocol(self),
                local_addr=('0.0.0.0', self.local_port),
            )
            print(f"✅ UDP socket bound to 0.0.0.0:{self.local_port}")
        
        self.running = True
        
        # Register with server
        await self.register()
        
        # Start registration refresh loop
        self._registration_refresh_task = asyncio.create_task(self._registration_refresh_loop())
        
        # Start OPTIONS keep-alive for UDP (RFC 5626 compliant)
        if self.transport == "udp":
            self._options_keepalive_task = asyncio.create_task(self._options_keepalive_loop())
            # Start connection health monitoring
            self._options_health_check_task = asyncio.create_task(self._options_health_check_loop())
    
    async def stop(self):
        """Stop the SIP client."""
        self.running = False
        
        # Cancel reconnect task if running
        if self.reconnect_task:
            self.reconnect_task.cancel()
            try:
                await self.reconnect_task
            except asyncio.CancelledError:
                pass
        
        # Cancel registration refresh task
        if self._registration_refresh_task:
            self._registration_refresh_task.cancel()
            try:
                await self._registration_refresh_task
            except asyncio.CancelledError:
                pass
        
        # Cancel OPTIONS keep-alive task
        if self._options_keepalive_task:
            self._options_keepalive_task.cancel()
            try:
                await self._options_keepalive_task
            except asyncio.CancelledError:
                pass
        
        # Cancel OPTIONS health check task
        if self._options_health_check_task:
            self._options_health_check_task.cancel()
            try:
                await self._options_health_check_task
            except asyncio.CancelledError:
                pass
        
        # Cancel any pending registration timeouts
        for call_id, pending_info in list(self.pending_registrations.items()):
            if "task" in pending_info and pending_info["task"]:
                pending_info["task"].cancel()
            del self.pending_registrations[call_id]
        
        # Unregister
        if self.registered:
            await self.unregister()
        
        if self.transport_obj:
            self.transport_obj.close()
    
    def _calculate_digest_auth(self, method: str, uri: str, realm: str, nonce: str, username: str, password: str, cseq: int, call_id: str, opaque: Optional[str] = None) -> str:
        """Calculate SIP Digest Authentication response."""
        # A1 = MD5(username:realm:password)
        a1 = f"{username}:{realm}:{password}"
        ha1 = hashlib.md5(a1.encode()).hexdigest()
        
        # A2 = MD5(method:uri)
        a2 = f"{method}:{uri}"
        ha2 = hashlib.md5(a2.encode()).hexdigest()
        
        # Response = MD5(HA1:nonce:HA2)
        response_str = f"{ha1}:{nonce}:{ha2}"
        response = hashlib.md5(response_str.encode()).hexdigest()
        
        # Build Authorization header
        auth_header = f'Digest username="{username}", realm="{realm}", nonce="{nonce}", uri="{uri}", response="{response}"'
        if opaque:
            auth_header += f', opaque="{opaque}"'
        
        return auth_header
    
    async def register(self, with_auth: bool = False):
        """Register with the SIP server."""
        if not with_auth:
            # First registration attempt - generate new call ID
            self.registration_call_id = self._generate_call_id()
            self.cseq = 1
            self.auth_retry_count = 0
        else:
            # For auth retry, use existing call_id but increment CSeq
            # CSeq must increment for each REGISTER request (RFC 3261)
            if not self.registration_call_id:
                self.registration_call_id = self._generate_call_id()
        
        call_id = self.registration_call_id
        branch = self._generate_branch()
        cseq = self.cseq
        self.cseq += 1  # Always increment CSeq for each REGISTER request
        
        if not with_auth:
            print(f"Attempting SIP registration with {self.server}:{self.server_port}...")
            print(f"  Username: {self.username}")
            print(f"  Binding to: {self.local_ip}:{self.local_port}")
            print(f"  Will advertise Contact: {self.local_ip}:{self.local_port}")
        else:
            print(f"Retrying registration with digest authentication...")
        
        # Create REGISTER request
        # Send to server_port (where FritzBox listens, typically 5060)
        uri = f"sip:{self.server}"
        request = (
            f"REGISTER {uri} SIP/2.0\r\n"
            f"Via: SIP/2.0/UDP {self.local_ip}:{self.local_port};branch={branch}\r\n"
            f"From: <sip:{self.username}@{self.server}>;tag={self.tag}\r\n"
            f"To: <sip:{self.username}@{self.server}>\r\n"
            f"Call-ID: {call_id}\r\n"
            f"CSeq: {cseq} REGISTER\r\n"
            f"Contact: <sip:{self.username}@{self.local_ip}:{self.local_port}>\r\n"
            f"Expires: 3600\r\n"
            f"User-Agent: HA-Voice-Assistant/0.1\r\n"
        )
        
        # Add Authorization header if we have auth info
        if with_auth and self.auth_realm and self.auth_nonce:
            auth_header = self._calculate_digest_auth(
                "REGISTER", uri, self.auth_realm, self.auth_nonce,
                self.username, self.password, cseq, call_id, self.auth_opaque
            )
            request += f"Authorization: {auth_header}\r\n"
            print(f"   CSeq: {cseq}, Nonce: {self.auth_nonce[:20]}...")
        
        request += f"Content-Length: 0\r\n\r\n"
        
        # Clean up any old pending registrations for this call_id (but different CSeq)
        # This prevents accumulation of timeout handlers for old requests
        keys_to_remove = [key for key in self.pending_registrations.keys() if key.startswith(f"{call_id}:")]
        for key in keys_to_remove:
            old_info = self.pending_registrations[key]
            if "task" in old_info and old_info["task"]:
                old_info["task"].cancel()
            del self.pending_registrations[key]
        
        # Track pending registration for timeout detection
        pending_key = f"{call_id}:{cseq}"
        timeout_task = asyncio.create_task(self._registration_timeout_handler(call_id, cseq))
        self.pending_registrations[pending_key] = {
            "timestamp": time.time(),
            "task": timeout_task,
            "with_auth": with_auth,
        }
        
        # Send REGISTER to server_port (where FritzBox listens)
        self.transport_obj.sendto(
            request.encode(),
            (self.server, self.server_port),
        )
        
        if not with_auth:
            print(f"  REGISTER request sent (Call-ID: {call_id}, CSeq: {cseq})")
    
    async def unregister(self):
        """Unregister from the SIP server."""
        call_id = self._generate_call_id()
        branch = self._generate_branch()
        cseq = self.cseq
        self.cseq += 1
        
        request = (
            f"REGISTER sip:{self.server} SIP/2.0\r\n"
            f"Via: SIP/2.0/UDP {self.local_ip}:{self.local_port};branch={branch}\r\n"
            f"From: <sip:{self.username}@{self.server}>;tag={self.tag}\r\n"
            f"To: <sip:{self.username}@{self.server}>\r\n"
            f"Call-ID: {call_id}\r\n"
            f"CSeq: {cseq} REGISTER\r\n"
            f"Contact: <sip:{self.username}@{self.local_ip}:{self.local_port}>\r\n"
            f"Expires: 0\r\n"
            f"User-Agent: HA-Voice-Assistant/0.1\r\n"
            f"Content-Length: 0\r\n"
            f"\r\n"
        )
        
        # Send UNREGISTER to server_port (where FritzBox listens)
        self.transport_obj.sendto(
            request.encode(),
            (self.server, self.server_port),
        )
    
    async def _attempt_reconnection(self):
        """Attempt to reconnect with exponential backoff."""
        # Don't start multiple reconnect tasks
        if self.reconnect_task and not self.reconnect_task.done():
            return
        
        # Start reconnection task
        self.reconnect_task = asyncio.create_task(self._reconnection_loop())
    
    async def _reconnection_loop(self):
        """Reconnection loop with exponential backoff."""
        first_attempt = True
        while self.running and not self.registered:
            # Calculate backoff delay: immediate first attempt, then 1s, 2s, 4s, 8s, 16s, 32s, max 60s
            if first_attempt:
                delay = 0
                first_attempt = False
            else:
                delay = min(2 ** (self.reconnect_attempts - 1), 60)
            
            if delay > 0:
                print(f"🔄 Attempting reconnection (attempt {self.reconnect_attempts + 1}, waiting {delay}s)...")
                await asyncio.sleep(delay)
            else:
                print(f"🔄 Attempting reconnection (attempt {self.reconnect_attempts + 1})...")
            
            if not self.running:
                break
            
            # Attempt registration
            self.reconnect_attempts += 1
            await self.register()
            
            # Wait a bit to see if registration succeeds
            await asyncio.sleep(2)
            
            # If we're now registered, break out of loop
            if self.registered:
                print(f"✅ Reconnection successful after {self.reconnect_attempts} attempt(s)")
                self.reconnect_attempts = 0
                break
    
    async def _registration_timeout_handler(self, call_id: str, cseq: int):
        """Handle timeout for REGISTER request if no response received."""
        await asyncio.sleep(self.registration_timeout)
        
        pending_key = f"{call_id}:{cseq}"
        if pending_key in self.pending_registrations:
            # Timeout occurred - no response received
            print(f"⚠️  REGISTER request timeout (Call-ID: {call_id}, CSeq: {cseq}) - no response received")
            
            # Mark as unregistered if we were expecting a response
            if self.registered:
                print(f"⚠️  Marking as unregistered due to registration timeout")
                self.registered = False
            
            # Clean up pending registration
            del self.pending_registrations[pending_key]
            
            # Attempt reconnection if still running
            if self.running:
                await self._attempt_reconnection()
    
    async def _options_keepalive_loop(self):
        """Send periodic OPTIONS requests for keep-alive (RFC 5626 compliant for UDP)."""
        # Send OPTIONS every 30 seconds to keep NAT binding alive
        # This is more frequent than REGISTER refresh but lighter weight
        keepalive_interval = 30
        
        while self.running:
            await asyncio.sleep(keepalive_interval)
            
            if not self.running:
                break
            
            # Only send OPTIONS if we're registered and no active calls
            if not self.registered:
                continue
            
            if len(self.active_calls) > 0:
                continue  # Skip during active calls
            
            # Send OPTIONS keep-alive
            await self._send_options_keepalive()
    
    async def _options_health_check_loop(self):
        """Monitor OPTIONS keep-alive responses to detect connection failures."""
        while self.running:
            await asyncio.sleep(self.options_health_check_interval)
            
            if not self.running:
                break
            
            # Only check if we're registered
            if not self.registered:
                continue
            
            # Skip during active calls
            if len(self.active_calls) > 0:
                continue
            
            current_time = time.time()
            
            # Check if we've sent OPTIONS but haven't received a response
            if self.last_options_send_time and not self.last_options_response_time:
                # We sent OPTIONS but never got a response
                time_since_send = current_time - self.last_options_send_time
                if time_since_send > self.options_max_no_response_time:
                    print(f"⚠️  No OPTIONS response received for {time_since_send:.0f} seconds - connection may be dead")
                    print(f"⚠️  Marking as unregistered and attempting re-registration")
                    self.registered = False
                    await self._attempt_reconnection()
                    continue
            
            # Check if we haven't received a response in a while
            if self.last_options_response_time:
                time_since_response = current_time - self.last_options_response_time
                if time_since_response > self.options_max_no_response_time:
                    # We haven't received a response in too long
                    print(f"⚠️  No OPTIONS response for {time_since_response:.0f} seconds - connection may be dead")
                    print(f"⚠️  Marking as unregistered and attempting re-registration")
                    self.registered = False
                    await self._attempt_reconnection()
    
    async def _send_options_keepalive(self):
        """Send OPTIONS request for keep-alive."""
        call_id = self._generate_call_id()
        branch = self._generate_branch()
        uri = f"sip:{self.server}"
        cseq = 1
        
        request = (
            f"OPTIONS {uri} SIP/2.0\r\n"
            f"Via: SIP/2.0/UDP {self.local_ip}:{self.local_port};branch={branch}\r\n"
            f"From: <sip:{self.username}@{self.server}>;tag={self.tag}\r\n"
            f"To: <sip:{self.username}@{self.server}>\r\n"
            f"Call-ID: {call_id}\r\n"
            f"CSeq: {cseq} OPTIONS\r\n"
            f"Contact: <sip:{self.username}@{self.local_ip}:{self.local_port}>\r\n"
            f"User-Agent: HA-Voice-Assistant/0.1\r\n"
        )
        
        # Add Authorization header if we have auth info (FritzBox requires it)
        if self.auth_realm and self.auth_nonce:
            auth_header = self._calculate_digest_auth(
                "OPTIONS", uri, self.auth_realm, self.auth_nonce,
                self.username, self.password, cseq, call_id, self.auth_opaque
            )
            request += f"Authorization: {auth_header}\r\n"
        
        request += f"Content-Length: 0\r\n\r\n"
        
        try:
            self.transport_obj.sendto(
                request.encode(),
                (self.server, self.server_port),
            )
            # Track when we sent OPTIONS
            self.last_options_send_time = time.time()
            # Debug: only log occasionally to avoid spam
            if not hasattr(self, '_last_options_log') or (time.time() - self._last_options_log) > 300:
                print(f"📡 OPTIONS keep-alive sent")
                self._last_options_log = time.time()
        except Exception as e:
            print(f"⚠️  Failed to send OPTIONS keep-alive: {e}")
            self.last_options_send_time = None
    
    async def _registration_refresh_loop(self):
        """Periodically refresh registration and check connection health."""
        while self.running:
            # Check every 60 seconds (1 minute) for connection health
            # This ensures we catch short expiry times and refresh in time
            await asyncio.sleep(60)
            
            if not self.running:
                break
            
            # Skip refresh if there are active calls - don't interrupt ongoing calls
            if len(self.active_calls) > 0:
                print(f"⏸️  Skipping registration refresh (active call in progress)")
                continue
            
            # Check if we need to refresh registration
            current_time = time.time()
            
            # If we have an expiry time and we're past 80% of the lifetime, refresh proactively
            if self.registration_expires_at and self.last_registration_time:
                lifetime = self.registration_expires_at - self.last_registration_time
                refresh_threshold = self.last_registration_time + (lifetime * 0.8)
                if current_time >= refresh_threshold:
                    print(f"🔄 Proactively refreshing registration (80% of expiry time reached)")
                    # Use auth if we have it (for refresh)
                    await self.register(with_auth=(self.auth_realm is not None))
            # If we're registered but haven't refreshed recently, do a health check
            elif self.registered:
                # If we haven't registered in the last 5 minutes, refresh (fallback check)
                if not self.last_registration_time or (current_time - self.last_registration_time) > 300:
                    print(f"🔄 Periodic registration refresh (health check)")
                    # Use auth if we have it (for refresh)
                    await self.register(with_auth=(self.auth_realm is not None))
            # If we're not registered, attempt reconnection
            elif not self.registered:
                print(f"⚠️  Not registered, attempting reconnection...")
                await self._attempt_reconnection()
    
    def _schedule_refresh_after_call(self):
        """Schedule registration refresh after call ends (if needed)."""
        if not self.running:
            return
        
        # Only refresh if there are no active calls
        if len(self.active_calls) > 0:
            return
        
        # Check if we need to refresh after call ends
        current_time = time.time()
        
        # If not registered, attempt reconnection
        if not self.registered:
            print(f"🔄 No active calls, attempting reconnection...")
            asyncio.create_task(self._attempt_reconnection())
        # If we're past 80% of expiry time, refresh immediately
        elif self.registration_expires_at and self.last_registration_time:
            lifetime = self.registration_expires_at - self.last_registration_time
            refresh_threshold = self.last_registration_time + (lifetime * 0.8)
            if current_time >= refresh_threshold:
                print(f"🔄 Call ended, refreshing registration (past 80% of expiry)")
                asyncio.create_task(self.register())
        # If we haven't registered recently and call ended, do a health check (fallback: 5 minutes)
        elif self.registered and (not self.last_registration_time or (current_time - self.last_registration_time) > 300):
            print(f"🔄 Call ended, refreshing registration (health check)")
            asyncio.create_task(self.register())
    
    def _handle_invite(self, headers: Dict[str, str], body: str, from_addr: tuple):
        """Handle incoming INVITE request."""
        call_id = headers.get("Call-ID", "")
        if not call_id:
            print(f"❌ INVITE received without Call-ID, cannot process")
            return
        
        from_header = headers.get("From", "")
        to_header = headers.get("To", "")
        
        # Extract caller ID
        caller_id = "unknown"
        try:
            caller_match = re.search(r'["\']?([^"\']+)["\']?\s*<?sip:([^>@]+)', from_header)
            if caller_match:
                caller_id = caller_match.group(2)
        except Exception as e:
            print(f"⚠️  Error extracting caller ID from '{from_header}': {e}")
        
        # Extract SDP for RTP addresses
        sdp_info = {}
        try:
            sdp_info = self._parse_sdp(body)
        except Exception as e:
            print(f"⚠️  Error parsing SDP: {e}")
            import traceback
            traceback.print_exc()
        
        # Create call info
        call_info = {
            "call_id": call_id,
            "caller_id": caller_id,
            "from_header": from_header,
            "to_header": to_header,
            "remote_ip": from_addr[0],
            "remote_port": from_addr[1],
            "rtp_info": sdp_info,
        }
        
        self.active_calls[call_id] = call_info
        print(f"📞 Added call {call_id} to active_calls")
        print(f"   Active calls now: {list(self.active_calls.keys())}")
        
        # Send 100 Trying
        try:
            self._send_response(call_id, 100, "Trying", from_addr, headers)
        except Exception as e:
            print(f"❌ Failed to send 100 Trying: {e}")
            raise
        
        # Send 180 Ringing
        try:
            self._send_response(call_id, 180, "Ringing", from_addr, headers)
        except Exception as e:
            print(f"❌ Failed to send 180 Ringing: {e}")
            raise
        
        # Send 200 OK with SDP
        try:
            self._send_200_ok(call_id, from_addr, headers, sdp_info)
        except Exception as e:
            print(f"❌ Failed to send 200 OK: {e}")
            import traceback
            traceback.print_exc()
            raise
        
        # Trigger callback
        if self.on_incoming_call:
            try:
                asyncio.create_task(self.on_incoming_call(caller_id, call_info))
            except Exception as e:
                print(f"❌ Failed to create callback task: {e}")
                import traceback
                traceback.print_exc()
    
    def _send_response(self, call_id: str, code: int, reason: str, to_addr: tuple, original_headers: Dict[str, str]):
        """Send a SIP response."""
        if not self.transport_obj:
            print(f"❌ Cannot send {code} {reason}: transport_obj is None")
            raise RuntimeError("Transport is not available")
        
        if self.transport_obj.is_closing():
            print(f"❌ Cannot send {code} {reason}: transport is closing")
            raise RuntimeError("Transport is closing")
        
        branch = self._generate_branch()
        cseq = original_headers.get("CSeq", "1 INVITE")
        
        response = (
            f"SIP/2.0 {code} {reason}\r\n"
            f"Via: {original_headers.get('Via', '')}\r\n"
            f"From: {original_headers.get('From', '')}\r\n"
            f"To: {original_headers.get('To', '')};tag={self.tag}\r\n"
            f"Call-ID: {call_id}\r\n"
            f"CSeq: {cseq}\r\n"
            f"Contact: <sip:{self.username}@{self.local_ip}:{self.local_port}>\r\n"
            f"Content-Length: 0\r\n"
            f"\r\n"
        )
        
        try:
            self.transport_obj.sendto(response.encode(), to_addr)
        except Exception as e:
            print(f"❌ Failed to send {code} {reason} via transport: {e}")
            raise
    
    def _send_200_ok(self, call_id: str, to_addr: tuple, original_headers: Dict[str, str], remote_sdp: Dict[str, Any]):
        """Send 200 OK with SDP."""
        branch = self._generate_branch()
        cseq = original_headers.get("CSeq", "1 INVITE")
        
        # Generate local RTP port
        rtp_port = random.randint(10000, 20000)
        
        # Check what codec FritzBox offers - prefer matching sample rate
        offered_codecs = remote_sdp.get("codecs", [])
        print(f"📞 FritzBox offered codecs: {offered_codecs}")
        
        # Look for PCMU (standard G.711)
        # IMPORTANT: G.711 is ALWAYS 8kHz, even if SDP says 16kHz!
        # FritzBox might incorrectly label PT=121 as "PCMU@16kHz"
        # But G.711 μ-law is always 8kHz, so we should use 8kHz internally
        
        preferred_pt = None
        preferred_sample_rate = 8000  # G.711 is always 8kHz!
        
        # Prefer standard PT=0 (PCMU @ 8kHz) if available
        if "0" in offered_codecs:
            preferred_pt = "0"
            preferred_sample_rate = 8000
            print(f"📞 Using standard PCMU @ 8kHz (PT=0)")
        else:
            # Fallback: Use PT=121 but still treat as 8kHz internally
            for pt in offered_codecs:
                codec_info = remote_sdp.get(pt, {})
                codec_name = codec_info.get("name", "")
                
                if "PCMU" in codec_name or codec_name == "PCMU":
                    preferred_pt = pt
                    # G.711 is always 8kHz, regardless of what SDP says!
                    preferred_sample_rate = 8000
                    print(f"📞 Found PCMU (PT={pt}), using as 8kHz (G.711 is always 8kHz)")
                    break
        
        if preferred_pt is None:
            # Default to standard PCMU @ 8kHz (PT=0)
            preferred_pt = "0"
            preferred_sample_rate = 8000
            print(f"📞 Using default PCMU @ 8kHz (PT=0)")
        
        codec_pt = str(preferred_pt)
        # In SDP, we can say 16kHz if FritzBox expects it, but process as 8kHz
        if int(preferred_pt) == 121:
            codec_name = "PCMU/16000"  # SDP says 16kHz for compatibility
        else:
            codec_name = f"PCMU/{preferred_sample_rate}"
        
        print(f"📞 Responding with {codec_name} (PT={codec_pt}), processing as 8kHz")
        
        # Create SDP
        sdp = (
            f"v=0\r\n"
            f"o=- {random.randint(1000000, 9999999)} {random.randint(1000000, 9999999)} IN IP4 {self.local_ip}\r\n"
            f"s=HA Voice Assistant\r\n"
            f"c=IN IP4 {self.local_ip}\r\n"
            f"t=0 0\r\n"
            f"m=audio {rtp_port} RTP/AVP {codec_pt}\r\n"
            f"a=rtpmap:{codec_pt} {codec_name}\r\n"
            f"a=sendrecv\r\n"
        )
        
        response = (
            f"SIP/2.0 200 OK\r\n"
            f"Via: {original_headers.get('Via', '')}\r\n"
            f"From: {original_headers.get('From', '')}\r\n"
            f"To: {original_headers.get('To', '')};tag={self.tag}\r\n"
            f"Call-ID: {call_id}\r\n"
            f"CSeq: {cseq}\r\n"
            f"Contact: <sip:{self.username}@{self.local_ip}:{self.local_port}>\r\n"
            f"Content-Type: application/sdp\r\n"
            f"Content-Length: {len(sdp)}\r\n"
            f"\r\n"
            f"{sdp}"
        )
        
        if not self.transport_obj:
            print(f"❌ Cannot send 200 OK: transport_obj is None")
            raise RuntimeError("Transport is not available")
        
        if self.transport_obj.is_closing():
            print(f"❌ Cannot send 200 OK: transport is closing")
            raise RuntimeError("Transport is closing")
        
        try:
            self.transport_obj.sendto(response.encode(), to_addr)
        except Exception as e:
            print(f"❌ Failed to send 200 OK via transport: {e}")
            raise
        
        # Store RTP port and codec info in call info
        if call_id in self.active_calls:
            self.active_calls[call_id]["local_rtp_port"] = rtp_port
            self.active_calls[call_id]["remote_rtp_port"] = remote_sdp.get("rtp_port", 0)
            self.active_calls[call_id]["remote_rtp_ip"] = remote_sdp.get("rtp_ip", to_addr[0])
            # Store codec type and sample rate for RTP session
            # IMPORTANT: G.711 is always 8kHz internally, regardless of SDP!
            self.active_calls[call_id]["codec"] = "g711"
            self.active_calls[call_id]["codec_pt"] = codec_pt
            self.active_calls[call_id]["sample_rate"] = 8000  # G.711 is always 8kHz!
    
    def _parse_sdp(self, sdp_body: str) -> Dict[str, Any]:
        """Parse SDP body to extract RTP information."""
        info = {
            "rtp_ip": self.server,
            "rtp_port": 0,
            "codecs": [],  # List of offered codecs
        }
        
        # Extract connection IP
        c_match = re.search(r'c=IN IP4 (\S+)', sdp_body)
        if c_match:
            info["rtp_ip"] = c_match.group(1)
        
        # Extract media port
        m_match = re.search(r'm=audio (\d+) RTP/AVP (.+)', sdp_body)
        if m_match:
            info["rtp_port"] = int(m_match.group(1))
            # Extract payload types (codecs)
            payload_types = m_match.group(2).strip().split()
            info["codecs"] = payload_types
        
        # Extract rtpmap for each codec
        for pt in info["codecs"]:
            rtpmap_match = re.search(rf'a=rtpmap:{pt} (\S+)/(\d+)', sdp_body)
            if rtpmap_match:
                codec_name = rtpmap_match.group(1)
                sample_rate = int(rtpmap_match.group(2))
                if pt not in info:
                    info[pt] = {}
                info[pt]["name"] = codec_name
                info[pt]["sample_rate"] = sample_rate
                print(f"📞 FritzBox bietet Codec: PT={pt}, {codec_name}@{sample_rate}Hz")
        
        return info
    
    def _handle_ack(self, headers: Dict[str, str], from_addr: tuple):
        """Handle ACK request (call established)."""
        call_id = headers.get("Call-ID", "")
        # Call is now established, RTP can start
        if call_id in self.active_calls:
            self.active_calls[call_id]["established"] = True
    
    def _handle_bye(self, headers: Dict[str, str], from_addr: tuple):
        """Handle BYE request (call ending)."""
        call_id = headers.get("Call-ID", "")
        
        # Send 200 OK immediately
        cseq = headers.get("CSeq", "1 BYE")
        response = (
            f"SIP/2.0 200 OK\r\n"
            f"Via: {headers.get('Via', '')}\r\n"
            f"From: {headers.get('From', '')}\r\n"
            f"To: {headers.get('To', '')}\r\n"
            f"Call-ID: {call_id}\r\n"
            f"CSeq: {cseq}\r\n"
            f"Content-Length: 0\r\n"
            f"\r\n"
        )
        
        self.transport_obj.sendto(response.encode(), from_addr)
        
        # Mark call as ended, but don't remove it yet - let the session clean up
        if call_id in self.active_calls:
            self.active_calls[call_id]["ended"] = True
            print(f"📞 Marked call {call_id} as ended (session will clean up)")
            # Don't refresh here - call is still in active_calls, refresh will happen after cleanup
        else:
            print(f"⚠️  BYE received for call {call_id}, but call not in active_calls")
            print(f"   Active calls: {list(self.active_calls.keys())}")
    
    def get_call_info(self, call_id: str) -> Optional[Dict[str, Any]]:
        """Get information about an active call."""
        return self.active_calls.get(call_id)
    
    async def _handle_sip_message(self, message: str, from_addr: tuple):
        """Handle incoming SIP message."""
        try:
            # Debug: log received SIP messages
            first_line = message.split('\r\n')[0] if message else ""
            if first_line and (first_line.startswith("SIP/2.0") or first_line.startswith("INVITE") or first_line.startswith("ACK") or first_line.startswith("BYE")):
                print(f"📨 Received SIP message from {from_addr[0]}:{from_addr[1]}: {first_line[:60]}...")
            
            lines = message.split('\r\n')
            if not lines:
                return
            
            request_line = lines[0]
            
            # Parse headers
            headers = {}
            body_start = 0
            for i, line in enumerate(lines[1:], 1):
                if not line.strip():
                    body_start = i + 1
                    break
                if ':' in line:
                    key, value = line.split(':', 1)
                    headers[key.strip()] = value.strip()
            
            # Get body
            body = '\r\n'.join(lines[body_start:]) if body_start < len(lines) else ""
            
            # Determine message type
            if request_line.startswith("SIP/2.0"):
                # Response
                try:
                    status_code = int(request_line.split()[1])
                    reason = " ".join(request_line.split()[2:]) if len(request_line.split()) > 2 else ""
                except (ValueError, IndexError):
                    print(f"⚠️  Invalid SIP response: {request_line}")
                    return
                
                # Extract Call-ID for response matching
                call_id = headers.get("Call-ID", "")
                
                if status_code == 200:
                    # Check if this is a REGISTER response
                    cseq = headers.get("CSeq", "")
                    if "REGISTER" in cseq:
                        # Extract CSeq number to match with pending registration
                        try:
                            cseq_num = int(cseq.split()[0])
                        except (ValueError, IndexError):
                            cseq_num = None
                        
                        # Cancel timeout for this registration
                        if cseq_num is not None:
                            pending_key = f"{call_id}:{cseq_num}"
                            if pending_key in self.pending_registrations:
                                pending_info = self.pending_registrations[pending_key]
                                if "task" in pending_info and pending_info["task"]:
                                    pending_info["task"].cancel()
                                del self.pending_registrations[pending_key]
                        
                        # Parse Expires header to track registration lifetime
                        expires_header = headers.get("Expires", "3600")
                        try:
                            expires_seconds = int(expires_header)
                        except ValueError:
                            expires_seconds = 3600  # Default to 1 hour
                        
                        current_time = time.time()
                        self.last_registration_time = current_time
                        self.registration_expires_at = current_time + expires_seconds
                        
                        if not self.registered:
                            print(f"✅ SIP Registration successful! (200 {reason})")
                            print(f"   Registration expires in {expires_seconds} seconds")
                        else:
                            print(f"🔄 Registration refreshed successfully (expires in {expires_seconds} seconds)")
                        
                        self.registered = True
                        # Reset reconnection attempts on successful registration
                        self.reconnect_attempts = 0
                        # Reset OPTIONS health tracking on successful registration
                        self.last_options_response_time = time.time()
                        self.last_options_send_time = None
                        # Log Contact header for debugging
                        contact_header = headers.get("Contact", "")
                        print(f"   Registration Contact header: {contact_header}")
                        print(f"   Our advertised Contact: <sip:{self.username}@{self.local_ip}:{self.local_port}>")
                    elif "OPTIONS" in cseq:
                        # OPTIONS keep-alive response - connection is alive
                        self.last_options_response_time = time.time()
                        if not hasattr(self, '_last_options_log') or (time.time() - self._last_options_log) > 300:
                            print(f"📡 OPTIONS keep-alive response received (connection alive)")
                            self._last_options_log = time.time()
                elif status_code == 401:
                    # Extract digest authentication challenge
                    cseq = headers.get("CSeq", "")
                    
                    # Handle OPTIONS 401 - extract auth and retry
                    if "OPTIONS" in cseq:
                        www_auth = headers.get("WWW-Authenticate", headers.get("Proxy-Authenticate", ""))
                        if www_auth and "Digest" in www_auth:
                            # Parse digest challenge
                            realm_match = re.search(r'realm="([^"]+)"', www_auth)
                            nonce_match = re.search(r'nonce="([^"]+)"', www_auth)
                            opaque_match = re.search(r'opaque="([^"]+)"', www_auth)
                            
                            if realm_match and nonce_match:
                                # Update auth info if we got new challenge
                                self.auth_realm = realm_match.group(1)
                                self.auth_nonce = nonce_match.group(1)
                                self.auth_opaque = opaque_match.group(1) if opaque_match else None
                                
                                # Track that we received a response (even if 401, connection is alive)
                                self.last_options_response_time = time.time()
                                
                                # Retry OPTIONS with auth (small delay to avoid rapid loops)
                                print(f"📡 OPTIONS 401 received, retrying with authentication...")
                                await asyncio.sleep(0.1)
                                await self._send_options_keepalive()
                        return
                    
                    if "REGISTER" in cseq:
                        # Extract CSeq number to match with pending registration
                        try:
                            cseq_num = int(cseq.split()[0])
                        except (ValueError, IndexError):
                            cseq_num = None
                        
                        # Cancel timeout for this registration (we got a response, even if 401)
                        if cseq_num is not None:
                            pending_key = f"{call_id}:{cseq_num}"
                            if pending_key in self.pending_registrations:
                                pending_info = self.pending_registrations[pending_key]
                                if "task" in pending_info and pending_info["task"]:
                                    pending_info["task"].cancel()
                                # Don't delete yet - we'll retry with auth
                        
                        www_auth = headers.get("WWW-Authenticate", headers.get("Proxy-Authenticate", ""))
                        if www_auth and "Digest" in www_auth:
                            print(f"⚠️  SIP Registration requires authentication (401 {reason})")
                            print("   Extracting digest challenge...")
                            
                            # Parse digest challenge
                            realm_match = re.search(r'realm="([^"]+)"', www_auth)
                            nonce_match = re.search(r'nonce="([^"]+)"', www_auth)
                            opaque_match = re.search(r'opaque="([^"]+)"', www_auth)
                            
                            if realm_match and nonce_match:
                                self.auth_realm = realm_match.group(1)
                                self.auth_nonce = nonce_match.group(1)
                                self.auth_opaque = opaque_match.group(1) if opaque_match else None
                                
                                print(f"   Realm: {self.auth_realm}")
                                print(f"   Retrying with digest authentication...")
                                
                                # Clean up old pending registration
                                if cseq_num is not None:
                                    pending_key = f"{call_id}:{cseq_num}"
                                    if pending_key in self.pending_registrations:
                                        del self.pending_registrations[pending_key]
                                
                                # Retry registration with authentication (limit retries)
                                self.auth_retry_count += 1
                                if self.auth_retry_count < 3:  # Max 2 retries
                                    await asyncio.sleep(0.3)
                                    await self.register(with_auth=True)
                                else:
                                    print("   ❌ Too many authentication attempts, giving up")
                                    # Clean up pending registration
                                    if cseq_num is not None:
                                        pending_key = f"{call_id}:{cseq_num}"
                                        if pending_key in self.pending_registrations:
                                            del self.pending_registrations[pending_key]
                            else:
                                print("   ⚠️  Could not parse digest challenge")
                        else:
                            print(f"⚠️  SIP Registration requires authentication (401 {reason})")
                            print("   Unknown authentication method")
                elif status_code >= 400:
                    # Check if this is a REGISTER response
                    cseq = headers.get("CSeq", "")
                    
                    # Handle OPTIONS errors gracefully (not critical for keep-alive)
                    if "OPTIONS" in cseq:
                        # OPTIONS keep-alive got an error - but we got a response, so connection is alive
                        self.last_options_response_time = time.time()
                        # Log but don't treat as critical
                        if not hasattr(self, '_last_options_error_log') or (time.time() - self._last_options_error_log) > 300:
                            print(f"📡 OPTIONS keep-alive: {status_code} response (connection alive, non-critical)")
                            self._last_options_error_log = time.time()
                        return
                    
                    if "REGISTER" in cseq:
                        # Extract CSeq number to match with pending registration
                        try:
                            cseq_num = int(cseq.split()[0])
                        except (ValueError, IndexError):
                            cseq_num = None
                        
                        # Cancel timeout for this registration (we got a response)
                        if cseq_num is not None:
                            pending_key = f"{call_id}:{cseq_num}"
                            if pending_key in self.pending_registrations:
                                pending_info = self.pending_registrations[pending_key]
                                if "task" in pending_info and pending_info["task"]:
                                    pending_info["task"].cancel()
                                del self.pending_registrations[pending_key]
                        
                        print(f"❌ SIP Registration failed: {status_code} {reason}")
                        # Mark as unregistered on failure
                        if self.registered:
                            print(f"⚠️  Marking as unregistered due to registration failure")
                            self.registered = False
                        
                        # For certain error codes, attempt reconnection
                        if status_code in (401, 403, 408, 500, 503):
                            # Don't reconnect immediately for 401 - let auth retry handle it
                            if status_code != 401:
                                await self._attempt_reconnection()
                        elif status_code >= 400:
                            # For other 4xx/5xx errors, attempt reconnection
                            await self._attempt_reconnection()
            elif request_line.startswith("INVITE"):
                print(f"🚨 INVITE REQUEST PROCESSING: {request_line[:60]}")
                print(f"   Registered: {self.registered}")
                print(f"   Transport: {self.transport_obj is not None}")
                if self.transport_obj:
                    print(f"   Transport closing: {self.transport_obj.is_closing()}")
                try:
                    self._handle_invite(headers, body, from_addr)
                except Exception as e:
                    print(f"❌ Error handling INVITE: {e}")
                    import traceback
                    traceback.print_exc()
                    # Try to send error response if we have headers
                    try:
                        call_id = headers.get("Call-ID", "")
                        if call_id:
                            self._send_response(call_id, 500, "Internal Server Error", from_addr, headers)
                    except Exception as send_error:
                        print(f"❌ Failed to send error response: {send_error}")
            elif request_line.startswith("ACK"):
                try:
                    self._handle_ack(headers, from_addr)
                except Exception as e:
                    print(f"❌ Error handling ACK: {e}")
                    import traceback
                    traceback.print_exc()
            elif request_line.startswith("BYE"):
                try:
                    self._handle_bye(headers, from_addr)
                except Exception as e:
                    print(f"❌ Error handling BYE: {e}")
                    import traceback
                    traceback.print_exc()
        except Exception as e:
            print(f"❌ Error in _handle_sip_message: {e}")
            import traceback
            traceback.print_exc()


class SIPProtocol(asyncio.DatagramProtocol):
    """Protocol handler for SIP messages."""
    
    def __init__(self, sip_client: SIPClient):
        self.sip_client = sip_client
        self.transport = None
    
    def connection_made(self, transport):
        self.transport = transport
    
    def datagram_received(self, data, addr):
        print(f"📦 Raw datagram received from {addr}: {len(data)} bytes")
        message = data.decode('utf-8', errors='ignore')
        first_line = message.split('\r\n')[0] if message else ""
        
        # Critical: Check if this is an INVITE
        if first_line.startswith("INVITE"):
            print(f"🚨 INVITE DETECTED! First line: {first_line}")
            print(f"   Registered status: {self.sip_client.registered}")
            print(f"   Transport available: {self.sip_client.transport_obj is not None}")
            if self.sip_client.transport_obj:
                print(f"   Transport closing: {self.sip_client.transport_obj.is_closing()}")
        
        print(f"📝 Decoded message (first 100 chars): {message[:100]}")
        try:
            asyncio.create_task(self.sip_client._handle_sip_message(message, addr))
        except Exception as e:
            print(f"❌ Failed to create task for SIP message: {e}")
            import traceback
            traceback.print_exc()
    
    def error_received(self, exc):
        print(f"SIP protocol error: {exc}")
    
    def connection_lost(self, exc):
        """Handle connection loss - trigger reconnection."""
        if exc:
            print(f"⚠️  SIP connection lost: {exc}")
        else:
            print(f"⚠️  SIP connection lost (unknown reason)")
        
        # Mark as unregistered
        if self.sip_client.registered:
            print(f"⚠️  Marking as unregistered due to connection loss")
            self.sip_client.registered = False
        
        # Trigger reconnection attempt
        if self.sip_client.running:
            asyncio.create_task(self.sip_client._attempt_reconnection())

