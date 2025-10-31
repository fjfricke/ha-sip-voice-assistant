"""Audio adapter - resampling dynamically based on SIP codec ↔ 24kHz with resampy."""
import asyncio
import numpy as np
import resampy
from typing import Optional

# OpenAI sample rate
OPENAI_SAMPLE_RATE = 24000  # OpenAI Realtime API uses 24kHz


class AudioAdapter:
    """Adapter for audio format conversion and buffering."""
    
    # Frame sizes
    FRAME_SIZE_MS = 20
    
    def __init__(self, sample_rate: int = 8000):
        """Initialize adapter with specific sample rate (8kHz or 16kHz)."""
        self.sip_sample_rate = sample_rate
        self.pcm16_frame_size_sip = (sample_rate * self.FRAME_SIZE_MS * 2) // 1000  # PCM16 bytes per 20ms
        self.pcm16_frame_size_24k = 960  # 20ms * 24000Hz * 2 bytes
        
        print(f"📞 AudioAdapter: {sample_rate}Hz → 24kHz (SIP frame: {self.pcm16_frame_size_sip} bytes)")
        
        # Queues for audio data (all at SIP sample rate)
        self.uplink_queue: asyncio.Queue = asyncio.Queue()  # SIP → AI (PCM16 at SIP rate)
        self.downlink_queue: asyncio.Queue = asyncio.Queue()  # AI → SIP (PCM16 at SIP rate)
        
        # Downlink buffer for variable-size AI chunks
        self.downlink_buffer: bytearray = bytearray()
    
    async def send_uplink(self, pcm16_data: bytes):
        """Send audio data to AI (uplink) - expects 8kHz PCM16."""
        await self.uplink_queue.put(pcm16_data)
    
    async def get_uplink(self) -> bytes:
        """Get audio data for AI (uplink) - resamples SIP rate to 24kHz using resampy."""
        try:
            # Wait for audio data - shorter timeout to avoid gaps
            audio_sip = await asyncio.wait_for(self.uplink_queue.get(), timeout=0.02)
            
            # Verify frame size
            expected_size = self.pcm16_frame_size_sip
            if len(audio_sip) != expected_size:
                print(f"⚠️  Unexpected frame size: expected {expected_size} bytes, got {len(audio_sip)}")
            
            # Resample SIP sample rate to 24kHz using resampy library
            samples_sip = np.frombuffer(audio_sip, dtype=np.int16).astype(np.float32) / 32768.0
            samples_24k = resampy.resample(samples_sip, self.sip_sample_rate, OPENAI_SAMPLE_RATE)
            
            # Convert back to int16
            audio_24k = (np.clip(samples_24k, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
            
            # Verify output size
            if len(audio_24k) != self.pcm16_frame_size_24k:
                print(f"⚠️  Resampling output size: expected {self.pcm16_frame_size_24k} bytes, got {len(audio_24k)}")
            
            return audio_24k
        except asyncio.TimeoutError:
            # Return silence at 24kHz if queue is empty
            # This should rarely happen if packets come every 20ms
            return b'\x00' * self.pcm16_frame_size_24k
    
    async def send_downlink(self, pcm16_data: bytes):
        """Send audio data from AI (downlink) - expects 24kHz PCM16, resamples to SIP rate."""
        # Resample 24kHz to SIP sample rate using resampy library
        samples_24k = np.frombuffer(pcm16_data, dtype=np.int16).astype(np.float32) / 32768.0
        samples_sip = resampy.resample(samples_24k, OPENAI_SAMPLE_RATE, self.sip_sample_rate)
        
        # Convert back to int16
        audio_sip = (np.clip(samples_sip, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
        
        # Accumulate and split to fixed SIP rate frames
        self.downlink_buffer.extend(audio_sip)
        
        # Split into fixed-size frames (SIP rate: X bytes per 20ms)
        while len(self.downlink_buffer) >= self.pcm16_frame_size_sip:
            frame = bytes(self.downlink_buffer[:self.pcm16_frame_size_sip])
            self.downlink_buffer = self.downlink_buffer[self.pcm16_frame_size_sip:]
            await self.downlink_queue.put(frame)
    
    async def get_downlink(self) -> bytes:
        """Get audio data for SIP (downlink) - returns PCM16 at SIP sample rate."""
        try:
            # Wait for audio with timeout matching frame interval (20ms)
            return await asyncio.wait_for(self.downlink_queue.get(), timeout=0.02)
        except asyncio.TimeoutError:
            # Return silence if no data available (at SIP rate)
            # This should be rare if audio is streaming continuously
            return b'\x00' * self.pcm16_frame_size_sip
    
    def clear_buffers(self):
        """Clear all audio buffers."""
        while not self.uplink_queue.empty():
            try:
                self.uplink_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        
        while not self.downlink_queue.empty():
            try:
                self.downlink_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        
        self.downlink_buffer = bytearray()
