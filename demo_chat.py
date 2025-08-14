#!/usr/bin/env python3
"""
Demonstration script showing chat history functionality
"""

import requests
import json
import time

BASE_URL = "http://127.0.0.1:8000"

def demo_chat_history():
    print("🎤 AI Voice Agent Chat History Demo")
    print("=" * 50)
    
    # Step 1: Create a new session
    print("\n1️⃣ Creating new chat session...")
    try:
        response = requests.post(f"{BASE_URL}/agent/session")
        if response.status_code == 200:
            session_data = response.json()
            session_id = session_data["session_id"]
            print(f"✅ Session created: {session_id[:8]}...")
        else:
            print(f"❌ Failed to create session: {response.status_code}")
            return
    except Exception as e:
        print(f"❌ Error creating session: {e}")
        return
    
    # Step 2: Simulate multiple chat interactions
    print("\n2️⃣ Simulating chat interactions...")
    
    # Simulate user messages (in a real scenario, these would be audio files)
    user_messages = [
        "Hello, my name is Alice. I'm interested in learning about artificial intelligence.",
        "What are the main types of machine learning?",
        "Can you explain deep learning in simple terms?",
        "What programming languages are best for AI development?"
    ]
    
    ai_responses = [
        "Hello Alice! It's great to meet you. I'd be happy to help you learn about artificial intelligence. AI is a fascinating field that involves creating systems that can perform tasks that typically require human intelligence.",
        "Great question! There are three main types of machine learning: supervised learning (learning from labeled examples), unsupervised learning (finding patterns in unlabeled data), and reinforcement learning (learning through trial and error with rewards).",
        "Deep learning is like having a very sophisticated pattern recognition system inspired by how our brains work. It uses multiple layers of artificial neurons to process information, similar to how we might break down a complex problem into simpler parts.",
        "Python is the most popular language for AI development due to its simplicity and rich ecosystem of libraries like TensorFlow and PyTorch. Other good options include R for statistical analysis, Julia for high-performance computing, and C++ for production systems."
    ]
    
    # Simulate adding messages to chat history
    for i, (user_msg, ai_msg) in enumerate(zip(user_messages, ai_responses), 1):
        print(f"\n   💬 User message {i}: {user_msg[:50]}...")
        print(f"   🤖 AI response {i}: {ai_msg[:50]}...")
        
        # In a real scenario, these would be added via the /agent/chat/{session_id} endpoint
        # For demo purposes, we'll just show what the conversation would look like
        
        time.sleep(0.5)  # Small delay for demo effect
    
    # Step 3: Show chat history
    print(f"\n3️⃣ Retrieving chat history for session...")
    try:
        response = requests.get(f"{BASE_URL}/agent/chat/{session_id}")
        if response.status_code == 200:
            history_data = response.json()
            message_count = len(history_data["messages"])
            print(f"✅ Chat history retrieved: {message_count} messages")
            
            if message_count > 0:
                print("\n📋 Current conversation:")
                for i, msg in enumerate(history_data["messages"], 1):
                    role = "👤 You" if msg["role"] == "user" else "🤖 AI"
                    content = msg["content"][:100] + "..." if len(msg["content"]) > 100 else msg["content"]
                    print(f"   {i}. {role}: {content}")
            else:
                print("   📝 No messages yet - start a conversation!")
                
        else:
            print(f"❌ Failed to get chat history: {response.status_code}")
    except Exception as e:
        print(f"❌ Error getting chat history: {e}")
    
    # Step 4: Show how context would work
    print(f"\n4️⃣ Context awareness demonstration:")
    print("   🔄 When you ask follow-up questions, the AI will remember:")
    print("   • Your name (Alice)")
    print("   • Your interest in AI")
    print("   • Previous topics discussed (ML types, deep learning, programming)")
    print("   • The conversation flow and context")
    
    # Step 5: Provide testing instructions
    print(f"\n5️⃣ Ready for testing!")
    print(f"   🌐 Open in browser: {BASE_URL}/?session={session_id}")
    print(f"   🎤 Start recording and ask questions")
    print(f"   🔄 The AI will maintain context throughout the conversation")
    print(f"   📱 Share the URL to continue conversations on other devices")
    
    print(f"\n✨ Demo completed! Your session is ready for real conversation.")
    print(f"   Session ID: {session_id}")

if __name__ == "__main__":
    demo_chat_history()
