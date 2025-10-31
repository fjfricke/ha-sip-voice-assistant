#!/usr/bin/env python3
"""Test the application startup and SIP registration."""
import asyncio
import sys
import signal
from app.main import Application

class TestApplication:
    """Test wrapper for the application."""
    
    def __init__(self):
        self.app = Application()
        self.running = True
        
    async def run_test(self, duration=20):
        """Run the application for a specified duration."""
        print("=" * 60)
        print("Starting Application Test")
        print("=" * 60)
        print()
        
        try:
            # Start the application
            start_task = asyncio.create_task(self.app.start())
            
            # Wait for a bit to see initialization
            await asyncio.sleep(duration)
            
            # Stop the application
            print("\n" + "=" * 60)
            print("Stopping application...")
            await self.app.stop()
            
            # Cancel the start task
            start_task.cancel()
            try:
                await start_task
            except asyncio.CancelledError:
                pass
                
        except KeyboardInterrupt:
            print("\nInterrupted by user")
            await self.app.stop()
        except Exception as e:
            print(f"\n❌ Error during test: {e}")
            import traceback
            traceback.print_exc()
            await self.app.stop()

async def main():
    """Main test function."""
    test = TestApplication()
    await test.run_test(duration=15)

if __name__ == "__main__":
    try:
        asyncio.run(main())
        print("\n✅ Test completed")
    except KeyboardInterrupt:
        print("\n⚠️  Test interrupted")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

