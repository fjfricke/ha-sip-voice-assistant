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
        on_incoming_call: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None,
    ):
        self.server = server
        self.username = username
        self.password = password
        self.display_name = display_name
        self.transport = transport
        self.port = port
        self.on_incoming_call = on_incoming_call
        
        self.local_ip = self._get_local_ip()
        self.local_port = port
        
        self.transport_obj: Optional[asyncio.DatagramTransport] = None
        self.protocol: Optional[SIPProtocol] = None
        self.running = False
        
        # Registration state
        self.registered = False
        self.call_id_counter = 0
        self.cseq = 1
        self.tag = self._generate_tag()
        self.branch = None
        
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
            print(f"✅ UDP socket bound successfully")
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
        asyncio.create_task(self._registration_refresh_loop())
    
    async def stop(self):
        """Stop the SIP client."""
        self.running = False
        
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
        
        call_id = self.registration_call_id
        branch = self._generate_branch()
        cseq = self.cseq
        if not with_auth:
            self.cseq += 1  # Only increment on first attempt
        
        if not with_auth:
            print(f"Attempting SIP registration with {self.server}:{self.port}...")
            print(f"  Username: {self.username}")
            print(f"  Local address: {self.local_ip}:{self.local_port}")
        else:
            print(f"Retrying registration with digest authentication...")
        
        # Create REGISTER request
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
        
        self.transport_obj.sendto(
            request.encode(),
            (self.server, self.port),
        )
        
        if not with_auth:
            print(f"  REGISTER request sent (Call-ID: {call_id})")
    
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
        
        self.transport_obj.sendto(
            request.encode(),
            (self.server, self.port),
        )
    
    async def _registration_refresh_loop(self):
        """Periodically refresh registration."""
        while self.running:
            await asyncio.sleep(1800)  # Refresh every 30 minutes
            if self.running and self.registered:
                await self.register()
    
    def _handle_invite(self, headers: Dict[str, str], body: str, from_addr: tuple):
        """Handle incoming INVITE request."""
        call_id = headers.get("Call-ID", "")
        from_header = headers.get("From", "")
        to_header = headers.get("To", "")
        
        # Extract caller ID
        caller_match = re.search(r'["\']?([^"\']+)["\']?\s*<?sip:([^>@]+)', from_header)
        caller_id = caller_match.group(2) if caller_match else "unknown"
        
        # Extract SDP for RTP addresses
        sdp_info = self._parse_sdp(body)
        
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
        self._send_response(call_id, 100, "Trying", from_addr, headers)
        
        # Send 180 Ringing
        self._send_response(call_id, 180, "Ringing", from_addr, headers)
        
        # Send 200 OK with SDP
        self._send_200_ok(call_id, from_addr, headers, sdp_info)
        
        # Trigger callback
        if self.on_incoming_call:
            asyncio.create_task(self.on_incoming_call(caller_id, call_info))
    
    def _send_response(self, call_id: str, code: int, reason: str, to_addr: tuple, original_headers: Dict[str, str]):
        """Send a SIP response."""
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
        
        self.transport_obj.sendto(response.encode(), to_addr)
    
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
        
        self.transport_obj.sendto(response.encode(), to_addr)
        
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
        else:
            print(f"⚠️  BYE received for call {call_id}, but call not in active_calls")
            print(f"   Active calls: {list(self.active_calls.keys())}")
    
    def get_call_info(self, call_id: str) -> Optional[Dict[str, Any]]:
        """Get information about an active call."""
        return self.active_calls.get(call_id)
    
    async def _handle_sip_message(self, message: str, from_addr: tuple):
        """Handle incoming SIP message."""
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
            status_code = int(request_line.split()[1])
            reason = " ".join(request_line.split()[2:]) if len(request_line.split()) > 2 else ""
            
            if status_code == 200:
                # Check if this is a REGISTER response
                cseq = headers.get("CSeq", "")
                if "REGISTER" in cseq:
                    if not self.registered:
                        print(f"✅ SIP Registration successful! (200 {reason})")
                        self.registered = True
            elif status_code == 401:
                # Extract digest authentication challenge
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
                        
                        # Retry registration with authentication (limit retries)
                        self.auth_retry_count += 1
                        if self.auth_retry_count < 3:  # Max 2 retries
                            await asyncio.sleep(0.3)
                            await self.register(with_auth=True)
                        else:
                            print("   ❌ Too many authentication attempts, giving up")
                    else:
                        print("   ⚠️  Could not parse digest challenge")
                else:
                    print(f"⚠️  SIP Registration requires authentication (401 {reason})")
                    print("   Unknown authentication method")
            elif status_code >= 400:
                print(f"❌ SIP Registration failed: {status_code} {reason}")
        elif request_line.startswith("INVITE"):
            self._handle_invite(headers, body, from_addr)
        elif request_line.startswith("ACK"):
            self._handle_ack(headers, from_addr)
        elif request_line.startswith("BYE"):
            self._handle_bye(headers, from_addr)


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
        print(f"📝 Decoded message (first 100 chars): {message[:100]}")
        asyncio.create_task(self.sip_client._handle_sip_message(message, addr))
    
    def error_received(self, exc):
        print(f"SIP protocol error: {exc}")
    
    def connection_lost(self, exc):
        pass

