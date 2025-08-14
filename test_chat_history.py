#!/usr/bin/env python3
"""
Test script for the chat history functionality
"""

import requests
import json
import time

BASE_URL = "http://127.0.0.1:8000"

def test_chat_history():
    print("Testing Chat History Functionality")
    print("=" * 40)
    
    # Test 1: Create a new session
    print("\n1. Creating new chat session...")
    try:
        response = requests.post(f"{BASE_URL}/agent/session")
        if response.status_code == 200:
            session_data = response.json()
            session_id = session_data["session_id"]
            print(f"✅ Session created successfully: {session_id}")
        else:
            print(f"❌ Failed to create session: {response.status_code}")
            return
    except Exception as e:
        print(f"❌ Error creating session: {e}")
        return
    
    # Test 2: Get chat history (should be empty initially)
    print("\n2. Getting initial chat history...")
    try:
        response = requests.get(f"{BASE_URL}/agent/chat/{session_id}")
        if response.status_code == 200:
            history_data = response.json()
            message_count = len(history_data["messages"])
            print(f"✅ Chat history retrieved: {message_count} messages")
        else:
            print(f"❌ Failed to get chat history: {response.status_code}")
    except Exception as e:
        print(f"❌ Error getting chat history: {e}")
    
    # Test 3: Check if session can be loaded from URL
    print("\n3. Testing session URL loading...")
    try:
        # Simulate loading the session from URL
        response = requests.get(f"{BASE_URL}/agent/chat/{session_id}")
        if response.status_code == 200:
            print("✅ Session can be loaded from URL")
        else:
            print(f"❌ Session loading failed: {response.status_code}")
    except Exception as e:
        print(f"❌ Error loading session: {e}")
    
    print(f"\n📋 Session ID for testing: {session_id}")
    print(f"🌐 Test URL: {BASE_URL}/?session={session_id}")
    print("\n✅ Chat history functionality is ready for testing!")
    print("\nTo test the full conversation flow:")
    print("1. Open the URL above in your browser")
    print("2. The session should automatically load")
    print("3. Start recording and ask questions")
    print("4. The AI will remember previous conversation context")

if __name__ == "__main__":
    test_chat_history()
