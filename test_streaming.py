#!/usr/bin/env python3
"""
Simple test script to verify WebSocket audio streaming functionality
"""

import asyncio
import websockets
import json
import time

async def test_audio_streaming():
    """Test the audio streaming WebSocket endpoint"""
    uri = "ws://localhost:8000/ws/audio"
    
    try:
        async with websockets.connect(uri) as websocket:
            print("✅ Connected to WebSocket server")
            
            # Send some test binary data (simulating audio chunks)
            test_data = b"fake_audio_chunk_data_" * 100  # 2.4KB of fake data
            
            print(f"📤 Sending test audio chunk: {len(test_data)} bytes")
            await websocket.send(test_data)
            
            # Wait for response
            try:
                response = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                print(f"📥 Received response: {response}")
                
                # Parse JSON response
                try:
                    data = json.loads(response)
                    if data.get("type") == "audio_chunk_received":
                        print(f"✅ Audio chunk confirmed: {data['chunk_size']} bytes")
                        print(f"📁 File being saved as: {data['filename']}")
                    else:
                        print(f"⚠️ Unexpected response type: {data.get('type')}")
                except json.JSONDecodeError:
                    print(f"⚠️ Non-JSON response: {response}")
                    
            except asyncio.TimeoutError:
                print("⏰ Timeout waiting for response")
            
            # Send another chunk
            test_data2 = b"second_audio_chunk_" * 50
            print(f"📤 Sending second audio chunk: {len(test_data2)} bytes")
            await websocket.send(test_data2)
            
            try:
                response2 = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                print(f"📥 Received second response: {response2}")
            except asyncio.TimeoutError:
                print("⏰ Timeout waiting for second response")
            
            print("✅ Test completed successfully")
            
    except websockets.exceptions.ConnectionRefused:
        print("❌ Connection refused. Make sure the server is running on port 8000")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    print("🧪 Testing WebSocket Audio Streaming...")
    print("Make sure the server is running with: python app.py")
    print()
    
    asyncio.run(test_audio_streaming())
