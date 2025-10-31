"""RTP session for audio streaming."""
import asyncio
import struct
import time
from typing import Optional, Callable, Awaitable
from collections import deque


class RTPSession:
    """RTP session for bidirectional audio streaming."""
    
    FRAME_SIZE_MS = 20
    
    def __init__(
        self,
        local_ip: str,
        local_port: int,
        remote_ip: str,
        remote_port: int,
        ssrc: int,
        sample_rate: int = 8000,  # Default 8kHz, but can be 16kHz
        payload_type: int = 0,  # Default PCMU (PT=0), but can be PT=121 for 16kHz
        on_audio_received: Optional[Callable[[bytes], Awaitable[None]]] = None,
        on_audio_requested: Optional[Callable[[], Awaitable[bytes]]] = None,
    ):
        self.local_ip = local_ip
        self.local_port = local_port
        self.remote_ip = remote_ip
        self.remote_port = remote_port
        self.ssrc = ssrc
        self.sample_rate = sample_rate
        self.payload_type = payload_type
        self.on_audio_received = on_audio_received
        self.on_audio_requested = on_audio_requested
        
        # Calculate frame sizes based on sample rate
        self.BYTES_PER_FRAME = (sample_rate * self.FRAME_SIZE_MS) // 1000  # G.711 bytes per 20ms
        self.PCM16_BYTES_PER_FRAME = (sample_rate * self.FRAME_SIZE_MS * 2) // 1000  # PCM16 bytes per 20ms
        
        self.transport: Optional[asyncio.DatagramTransport] = None
        self.protocol: Optional[asyncio.DatagramProtocol] = None
        self.running = False
        self.sequence_number = 0
        self.timestamp = 0
        self.last_send_time = time.time()
        self.frame_interval = self.FRAME_SIZE_MS / 1000.0
        
        print(f"📞 RTP Session: {sample_rate}Hz, PT={payload_type}, Frame={self.BYTES_PER_FRAME} bytes")
        
        # Audio queues
        self.incoming_audio_queue: asyncio.Queue = asyncio.Queue()
        self.outgoing_audio_queue: asyncio.Queue = asyncio.Queue()
        
        # Tasks
        self.receive_task: Optional[asyncio.Task] = None
        self.send_task: Optional[asyncio.Task] = None
    
    async def start(self):
        """Start the RTP session."""
        loop = asyncio.get_event_loop()
        
        # Create UDP transport
        self.transport, self.protocol = await loop.create_datagram_endpoint(
            lambda: RTPProtocol(self),
            local_addr=(self.local_ip, self.local_port),
        )
        
        self.running = True
        
        # Start receive and send tasks
        self.receive_task = asyncio.create_task(self._receive_loop())
        self.send_task = asyncio.create_task(self._send_loop())
    
    async def stop(self):
        """Stop the RTP session."""
        self.running = False
        
        if self.receive_task:
            self.receive_task.cancel()
            try:
                await self.receive_task
            except asyncio.CancelledError:
                pass
        
        if self.send_task:
            self.send_task.cancel()
            try:
                await self.send_task
            except asyncio.CancelledError:
                pass
        
        if self.transport:
            self.transport.close()
    
    async def send_audio(self, audio_data: bytes):
        """Queue audio data for transmission."""
        await self.outgoing_audio_queue.put(audio_data)
    
    async def _receive_loop(self):
        """Receive loop for incoming RTP packets."""
        packet_count = 0
        while self.running:
            try:
                # Get audio from protocol
                audio_data = await self.incoming_audio_queue.get()
                
                packet_count += 1
                
                if self.on_audio_received:
                    await self.on_audio_received(audio_data)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"❌ Error in receive loop: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(0.01)
    
    async def _send_loop(self):
        """Send loop for outgoing RTP packets."""
        while self.running:
            try:
                # Get audio to send
                try:
                    audio_data = await asyncio.wait_for(
                        self.outgoing_audio_queue.get(),
                        timeout=self.frame_interval,
                    )
                except asyncio.TimeoutError:
                    # Send silence if no audio available (G.722 frame: 320 bytes)
                    audio_data = b'\x00' * self.BYTES_PER_FRAME
                
                # Send RTP packet
                await self._send_rtp_packet(audio_data)
                
                # Maintain 20ms frame timing
                current_time = time.time()
                elapsed = current_time - self.last_send_time
                sleep_time = max(0, self.frame_interval - elapsed)
                await asyncio.sleep(sleep_time)
                self.last_send_time = time.time()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Error in send loop: {e}")
                await asyncio.sleep(0.01)
    
    async def _send_rtp_packet(self, payload: bytes):
        """Send an RTP packet."""
        if not self.transport or not self.running:
            return
        
        # RTP header: V(2) P(1) X(1) CC(4) M(1) PT(7) sequence(16) timestamp(32) SSRC(32)
        # Version 2, Payload Type from config (can be 0 for 8kHz or 121 for 16kHz)
        rtp_header = struct.pack(
            "!BBHII",
            0x80,  # V=2, P=0, X=0, CC=0
            0x80 | (self.payload_type & 0x7F),  # M=0, PT from config
            self.sequence_number & 0xFFFF,
            self.timestamp,
            self.ssrc,
        )
        
        packet = rtp_header + payload
        self.transport.sendto(packet, (self.remote_ip, self.remote_port))
        
        self.sequence_number = (self.sequence_number + 1) & 0xFFFF
        # Timestamp increment: 20ms worth of samples
        # IMPORTANT: For G.711 (PCMU), always use 8kHz for timestamp, even if payload_type is 121
        # G.711 is always 8kHz internally, so timestamp should be based on 8kHz
        if self.payload_type in [0, 121]:  # PCMU (G.711) - always 8kHz
            samples_per_20ms = 160  # 20ms @ 8kHz = 160 samples (fixed for G.711)
        else:
            samples_per_20ms = (self.sample_rate * self.FRAME_SIZE_MS) // 1000
        self.timestamp += samples_per_20ms
    
    def _handle_rtp_packet(self, data: bytes, addr: tuple):
        """Handle incoming RTP packet."""
        if len(data) < 12:
            return
        
        # Parse RTP header
        header = struct.unpack("!BBHII", data[:12])
        version = (header[0] >> 6) & 0x3
        payload_type = header[1] & 0x7F
        
        if version != 2:
            return
        
        payload = data[12:]
        
        # Debug: Log first few packets
        if not hasattr(self, '_rtp_packet_count'):
            self._rtp_packet_count = 0
        self._rtp_packet_count += 1
        
        if self._rtp_packet_count <= 5:
            print(f"📦 RTP packet #{self._rtp_packet_count}: PT={payload_type}, payload={len(payload)} bytes from {addr[0]}:{addr[1]}")
        
        # Queue audio data for processing
        asyncio.create_task(self._queue_audio(payload))
    
    async def _queue_audio(self, audio_data: bytes):
        """Queue incoming audio data."""
        await self.incoming_audio_queue.put(audio_data)


class RTPProtocol(asyncio.DatagramProtocol):
    """Protocol handler for RTP packets."""
    
    def __init__(self, rtp_session: RTPSession):
        self.rtp_session = rtp_session
        self.transport = None
    
    def connection_made(self, transport):
        self.transport = transport
    
    def datagram_received(self, data, addr):
        self.rtp_session._handle_rtp_packet(data, addr)
    
    def error_received(self, exc):
        print(f"RTP protocol error: {exc}")
    
    def connection_lost(self, exc):
        pass

