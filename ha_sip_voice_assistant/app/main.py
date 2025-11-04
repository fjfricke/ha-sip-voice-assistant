"""Main entry point for the application."""
import asyncio
import signal
import sys
import argparse
from typing import Dict, Any, Optional
from app.config import Config
from app.sip.client import SIPClient
from app.bridge.call_session import CallSession


class Application:
    """Main application class."""
    
    def __init__(self):
        self.config = Config()
        self.sip_client: Optional[SIPClient] = None
        self.active_sessions: Dict[str, CallSession] = {}
        self.running = False
        self._shutdown_requested = False
        self._stopping = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        
        # Setup signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        print(f"Received signal {signum}, shutting down...")
        # Signal handlers must be fast and synchronous
        # Just set a flag - the main loop will check this
        self._shutdown_requested = True
        self.running = False
        
        # Try to wake up the event loop if it's sleeping
        if self._loop is not None and self._loop.is_running():
            try:
                # Schedule a no-op callback to wake up the loop
                self._loop.call_soon_threadsafe(lambda: None)
            except RuntimeError:
                pass  # Loop might be closing
    
    async def start(self):
        """Start the application."""
        # Store reference to event loop for signal handler
        self._loop = asyncio.get_running_loop()
        
        print("Loading configuration...")
        self.config.load()
        
        print("Starting SIP client...")
        sip_config = self.config.get_sip_config()
        
        self.sip_client = SIPClient(
            server=sip_config["server"],
            username=sip_config["username"],
            password=sip_config["password"],
            display_name=sip_config["display_name"],
            transport=sip_config["transport"],
            port=sip_config["port"],  # Server port (where FritzBox listens)
            bind_port=sip_config.get("bind_port"),  # Bind port (where we listen locally)
            on_incoming_call=self._handle_incoming_call,
        )
        
        await self.sip_client.start()
        self.running = True
        
        print("SIP client started. Waiting for calls...")
        print(f"Registered as: {sip_config['username']}@{sip_config['server']}")
        
        # Wait a moment to see registration result
        await asyncio.sleep(2)
        if self.sip_client.registered:
            print("✅ SIP registration confirmed and ready for calls")
        else:
            print("⚠️  SIP registration status: waiting for response...")
        
        # Keep running
        try:
            while self.running and not self._shutdown_requested:
                await asyncio.sleep(1)
            
            # If we exit the loop due to shutdown, call stop()
            if self._shutdown_requested:
                await self.stop()
        except KeyboardInterrupt:
            await self.stop()
    
    async def stop(self):
        """Stop the application."""
        # Prevent multiple calls to stop()
        if self._stopping:
            return
        
        self._stopping = True
        print("Stopping application...")
        self.running = False
        self._shutdown_requested = True
        
        # Stop all active sessions
        for session in list(self.active_sessions.values()):
            await session.stop()
        self.active_sessions.clear()
        
        # Stop SIP client
        if self.sip_client:
            await self.sip_client.stop()
        
        print("Application stopped.")
    
    async def _handle_incoming_call(self, caller_id: str, call_info: Dict[str, Any]):
        """Handle an incoming call."""
        call_id = call_info.get("call_id", "unknown")
        print(f"📞 Incoming call from {caller_id} (Call-ID: {call_id})")
        print(f"   Call info keys: {list(call_info.keys())}")
        print(f"   Active calls: {list(self.sip_client.active_calls.keys())}")
        
        # Check immediately - call should already be in active_calls
        call_info_full = self.sip_client.get_call_info(call_id)
        if not call_info_full:
            print(f"❌ Call {call_id} not found in active_calls!")
            print(f"   Available call IDs: {list(self.sip_client.active_calls.keys())}")
            # Try to use the call_info directly if it has all needed data
            if "rtp_info" in call_info and "local_rtp_port" in call_info:
                print(f"   Using call_info directly (has RTP info)")
                call_info_full = call_info
            else:
                return
        
        # Create call session
        session = CallSession(
            self.config,
            call_id,
            caller_id,
            call_info_full,
        )
        
        self.active_sessions[call_id] = session
        
        try:
            await session.start()
            print(f"✅ Call session {call_id} started")
            
            # Wait for call to end (monitor SIP client for BYE)
            # Check if call is marked as ended or removed
            while call_id in self.sip_client.active_calls:
                call_info_check = self.sip_client.get_call_info(call_id)
                if call_info_check and call_info_check.get("ended", False):
                    print(f"📞 Call {call_id} marked as ended")
                    break
                await asyncio.sleep(0.5)  # Check more frequently
            
            print(f"📞 Call {call_id} ended")
        except Exception as e:
            print(f"❌ Error in call session {call_id}: {e}")
            import traceback
            traceback.print_exc()
        finally:
            await session.stop()
            self.active_sessions.pop(call_id, None)
            
            # Now remove from active_calls after session is stopped
            if call_id in self.sip_client.active_calls:
                del self.sip_client.active_calls[call_id]
                print(f"🧹 Cleaned up call {call_id} from active_calls")
            
            # Schedule registration refresh now that call is fully cleaned up
            self.sip_client._schedule_refresh_after_call()


async def main(dry_run: bool = False):
    """Main entry point."""
    if dry_run:
        print("🔍 DRY RUN MODE - Configuration check only")
        print("=" * 60)
        
        config = Config()
        try:
            config.load()
            print("✅ Configuration loaded successfully")
            
            # Print configuration summary
            sip_config = config.get_sip_config()
            openai_config = config.get_openai_config()
            ha_config = config.get_homeassistant_config()
            
            print(f"\nSIP Configuration:")
            print(f"  Server: {sip_config['server']}")
            print(f"  Username: {sip_config['username']}")
            print(f"  Password: {'SET' if sip_config['password'] else 'NOT SET'}")
            
            print(f"\nOpenAI Configuration:")
            print(f"  Model: {openai_config['model']}")
            print(f"  API Key: {'SET' if openai_config['api_key'] else 'NOT SET'}")
            
            print(f"\nHome Assistant Configuration:")
            print(f"  URL: {ha_config['url']}")
            print(f"  Token: {'SET' if ha_config['token'] else 'NOT SET'}")
            
            print(f"\n✅ All configuration checks passed!")
            print("=" * 60)
            print("Run without --dry-run to start the SIP client")
            return
        except Exception as e:
            print(f"❌ Configuration error: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
    
    app = Application()
    await app.start()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HA SIP Voice Assistant")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Test configuration without starting SIP client",
    )
    args = parser.parse_args()
    
    try:
        asyncio.run(main(dry_run=args.dry_run))
    except KeyboardInterrupt:
        print("\nShutting down...")
        sys.exit(0)

