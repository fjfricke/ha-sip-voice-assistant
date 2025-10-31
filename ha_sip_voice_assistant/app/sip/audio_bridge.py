"""Audio bridge for codec conversion - supports G.711 @ 8kHz and PCM16 @ 16kHz."""
import asyncio
import numpy as np
from typing import Optional, Callable, Awaitable
from app.sip.rtp_session import RTPSession

# Use g711 library for G.711 μ-law codec (8kHz only)
try:
    import g711
    G711_AVAILABLE = True
except ImportError:
    G711_AVAILABLE = False
    raise RuntimeError("g711 package is required. Install with: poetry add g711")


def g711_ulaw_to_pcm16(ulaw_data: bytes, sample_rate: int = 8000) -> bytes:
    """
    Convert G.711 μ-law to PCM16 using g711 library.
    Returns PCM16 at sample_rate.
    IMPORTANT: G.711 is ALWAYS 8kHz internally, regardless of what sample_rate says!
    """
    if not G711_AVAILABLE:
        raise RuntimeError("g711 package is required")
    
    # g711.decode_ulaw handles all conversion logic (always 8kHz output)
    # G.711 is always 8kHz, so we decode at 8kHz
    float_samples = g711.decode_ulaw(ulaw_data)
    
    # Convert float32 to int16
    int16_samples = (float_samples * 32767.0).astype(np.int16)
    pcm16_8k = int16_samples.tobytes()
    
    # If 16kHz requested, upsample from 8kHz to 16kHz
    # But note: This shouldn't happen for G.711 (it's always 8kHz)
    if sample_rate == 16000:
        import resampy
        samples_8k = np.frombuffer(pcm16_8k, dtype=np.int16).astype(np.float32) / 32768.0
        samples_16k = resampy.resample(samples_8k, 8000, 16000)
        pcm16_16k = (np.clip(samples_16k, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
        return pcm16_16k
    
    return pcm16_8k


def pcm16_to_g711_ulaw(pcm16_data: bytes, sample_rate: int = 8000) -> bytes:
    """
    Convert PCM16 to G.711 μ-law using g711 library.
    Expects PCM16 at sample_rate (8kHz or 16kHz).
    If 16kHz, downsamples to 8kHz first.
    """
    if not G711_AVAILABLE:
        raise RuntimeError("g711 package is required")
    
    # If 16kHz, downsample to 8kHz first
    if sample_rate == 16000:
        import resampy
        samples_16k = np.frombuffer(pcm16_data, dtype=np.int16).astype(np.float32) / 32768.0
        samples_8k = resampy.resample(samples_16k, 16000, 8000)
        pcm16_data = (np.clip(samples_8k, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
    
    # Convert bytes to float32 using proper normalization
    int16_samples = np.frombuffer(pcm16_data, dtype=np.int16)
    float_samples = int16_samples.astype(np.float32) / 32768.0
    
    # g711.encode_ulaw handles all encoding logic (always 8kHz input expected)
    return g711.encode_ulaw(float_samples)


class RTPAudioBridge:
    """Bridge between RTP session and audio adapter."""
    
    def __init__(
        self,
        rtp_session: RTPSession,
        audio_adapter,
    ):
        self.rtp_session = rtp_session
        self.audio_adapter = audio_adapter
        # G.711 is always 8kHz, regardless of RTP session sample_rate
        # (which might be set to 16kHz for SDP compatibility)
        self.sample_rate = 8000  # G.711 is always 8kHz!
        self.running = False
        self.uplink_task: Optional[asyncio.Task] = None
        self.downlink_task: Optional[asyncio.Task] = None
        
        print(f"📞 AudioBridge: Processing as 8kHz (G.711 is always 8kHz)")
    
    async def start(self):
        """Start the audio bridge."""
        self.running = True
        
        # Set up RTP callbacks
        self.rtp_session.on_audio_received = self._handle_rtp_audio
        self.rtp_session.on_audio_requested = self._get_rtp_audio
        
        # Start bridge tasks
        self.uplink_task = asyncio.create_task(self._uplink_loop())
        self.downlink_task = asyncio.create_task(self._downlink_loop())
    
    async def stop(self):
        """Stop the audio bridge."""
        self.running = False
        
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
    
    async def _handle_rtp_audio(self, ulaw_data: bytes):
        """Handle incoming RTP audio (G.711 μ-law -> PCM16)."""
        # Convert G.711 to PCM16 - G.711 is always 8kHz
        try:
            # G.711 is always 8kHz, so decode directly (no sample_rate parameter needed)
            pcm16_data = g711_ulaw_to_pcm16(ulaw_data, 8000)  # Always 8kHz for G.711
            
            # Verify: 160 bytes G.711 should become 320 bytes PCM16 (160 samples * 2 bytes)
            if len(ulaw_data) == 160 and len(pcm16_data) != 320:
                print(f"⚠️  Warning: Expected 320 bytes PCM16 from 160 bytes G.711, got {len(pcm16_data)}")
            
            # Send to audio adapter (will resample to 24kHz for OpenAI)
            await self.audio_adapter.send_uplink(pcm16_data)
        except Exception as e:
            print(f"❌ Error in _handle_rtp_audio: {e}")
            import traceback
            traceback.print_exc()
    
    async def _get_rtp_audio(self) -> bytes:
        """Get audio for RTP transmission (PCM16 -> G.711 μ-law)."""
        # Get PCM16 from audio adapter (should be 8kHz for G.711)
        pcm16_data = await self.audio_adapter.get_downlink()
        
        # G.711 is always 8kHz, so convert directly (no sample_rate parameter needed)
        ulaw_data = pcm16_to_g711_ulaw(pcm16_data, 8000)  # Always 8kHz for G.711
        
        # Verify: 320 bytes PCM16 should become 160 bytes G.711
        if len(pcm16_data) == 320 and len(ulaw_data) != 160:
            print(f"⚠️  Warning: Expected 160 bytes G.711 from 320 bytes PCM16, got {len(ulaw_data)}")
        
        return ulaw_data
    
    async def _uplink_loop(self):
        """Uplink loop: RTP -> Audio Adapter."""
        while self.running:
            await asyncio.sleep(0.01)  # Let RTP session handle the loop
    
    async def _downlink_loop(self):
        """Downlink loop: Audio Adapter -> RTP."""
        import time
        last_send_time = time.time()
        frame_interval = 0.02  # 20ms frames
        
        while self.running:
            try:
                # Get audio from adapter (already at 8kHz)
                # This will timeout after 20ms and return silence if queue is empty
                audio_data = await self.audio_adapter.get_downlink()
                
                # Convert and send via RTP (g711 library handles conversion)
                ulaw_data = pcm16_to_g711_ulaw(audio_data, 8000)
                await self.rtp_session.send_audio(ulaw_data)
                
                # Maintain precise 20ms frame timing
                current_time = time.time()
                elapsed = current_time - last_send_time
                sleep_time = max(0, frame_interval - elapsed)
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
                last_send_time = time.time()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"❌ Error in downlink loop: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(0.01)
