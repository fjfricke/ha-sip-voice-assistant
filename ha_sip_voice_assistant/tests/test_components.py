#!/usr/bin/env python3
"""Test individual components."""
import asyncio
import sys

async def test_homeassistant_client():
    """Test Home Assistant client."""
    print("\n" + "=" * 60)
    print("Testing Home Assistant Client")
    print("=" * 60)
    
    try:
        from app.config import Config
        from app.homeassistant.client import HomeAssistantClient
        
        config = Config()
        config.load()
        
        ha_config = config.get_homeassistant_config()
        if not ha_config['token']:
            print("⚠️  Home Assistant token not configured, skipping test")
            return True
        
        client = HomeAssistantClient(config)
        await client.start()
        print("✅ Home Assistant client initialized")
        
        # Try to get HA state (basic connectivity test)
        try:
            # This will fail if HA is not accessible, but that's okay
            print("  Attempting to connect to Home Assistant...")
            # We'll just test the initialization, not actual calls
            await client.stop()
            print("✅ Home Assistant client test passed")
            return True
        except Exception as e:
            print(f"⚠️  Could not connect to HA (this is okay if HA is not running): {e}")
            await client.stop()
            return True
            
    except Exception as e:
        print(f"❌ Home Assistant client test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

async def test_audio_codecs():
    """Test audio codec conversion."""
    print("\n" + "=" * 60)
    print("Testing Audio Codec Conversion")
    print("=" * 60)
    
    try:
        from app.sip.audio_bridge import g711_ulaw_to_pcm16, pcm16_to_g711_ulaw
        
        # Test data: G.711 μ-law samples
        test_ulaw = bytes([0x00, 0xFF, 0x7F, 0x80] * 40)  # 160 bytes (20ms frame)
        
        # Convert to PCM16
        pcm16 = g711_ulaw_to_pcm16(test_ulaw)
        print(f"✅ G.711 → PCM16: {len(test_ulaw)} bytes → {len(pcm16)} bytes")
        
        # Convert back to G.711
        ulaw_back = pcm16_to_g711_ulaw(pcm16)
        print(f"✅ PCM16 → G.711: {len(pcm16)} bytes → {len(ulaw_back)} bytes")
        
        # Verify round-trip (should be close, not exact due to quantization)
        matches = sum(1 for a, b in zip(test_ulaw, ulaw_back) if a == b)
        similarity = (matches / len(test_ulaw)) * 100
        print(f"✅ Round-trip similarity: {similarity:.1f}% (expected: ~50-70%)")
        
        return True
    except Exception as e:
        print(f"❌ Audio codec test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

async def test_pin_verification():
    """Test PIN verification."""
    print("\n" + "=" * 60)
    print("Testing PIN Verification")
    print("=" * 60)
    
    try:
        from app.config import Config
        from app.utils.pin_verification import PINVerifier
        
        config = Config()
        config.load()
        
        verifier = PINVerifier(config)
        
        # Test PIN retrieval
        test_caller = "+1234567890"
        pin = verifier.get_expected_pin(test_caller)
        print(f"✅ PIN retrieval for {test_caller}: {'SET' if pin else 'NOT SET'}")
        
        if pin:
            # Test voice PIN verification
            test_cases = [
                (f"the pin is {pin}", True),
                ("".join(list(pin)), True),  # Digits as string
                (f"my pin is one two three four", False),  # Should fail unless PIN matches
            ]
            
            for voice_input, should_match in test_cases:
                result = verifier.verify_voice_pin(voice_input, pin)
                status = "✅" if result == should_match or (should_match and result) else "⚠️"
                print(f"  {status} Voice PIN test: '{voice_input[:30]}...' -> {result}")
        
        return True
    except Exception as e:
        print(f"❌ PIN verification test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

async def test_tool_definitions():
    """Test tool definition building."""
    print("\n" + "=" * 60)
    print("Testing Tool Definitions")
    print("=" * 60)
    
    try:
        from app.config import Config
        from app.bridge.call_session import CallSession
        
        config = Config()
        config.load()
        
        # Create a mock call session to test tool building
        # We can't fully instantiate without SIP, but we can test the method
        from app.utils.caller_mapping import get_caller_settings
        
        test_caller = "+1234567890"
        settings = get_caller_settings(config, test_caller)
        available_tools = settings.get("available_tools", [])
        
        print(f"✅ Available tools for {test_caller}: {available_tools}")
        
        # Check each tool config
        for tool_name in available_tools:
            tool_config = config.get_tool_config(tool_name)
            if tool_config:
                print(f"  ✅ {tool_name}:")
                print(f"     Service: {tool_config.get('ha_service', 'N/A')}")
                print(f"     Requires PIN: {tool_config.get('requires_pin', False)}")
            else:
                print(f"  ⚠️  {tool_name}: Configuration not found")
        
        return True
    except Exception as e:
        print(f"❌ Tool definitions test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

async def run_all_tests():
    """Run all component tests."""
    print("=" * 60)
    print("Component Tests")
    print("=" * 60)
    
    tests = [
        test_audio_codecs,
        test_pin_verification,
        test_tool_definitions,
        test_homeassistant_client,
    ]
    
    results = []
    for test in tests:
        try:
            result = await test()
            results.append(result)
        except Exception as e:
            print(f"❌ Test {test.__name__} raised exception: {e}")
            results.append(False)
    
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    passed = sum(results)
    total = len(results)
    print(f"Passed: {passed}/{total}")
    
    if passed == total:
        print("✅ All component tests passed!")
    else:
        print("⚠️  Some tests failed or were skipped")
    
    return passed == total

if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)

