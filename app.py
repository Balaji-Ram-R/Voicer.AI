import os
import certifi
os.environ['SSL_CERT_FILE'] = certifi.where()

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request as StarletteRequest
import assemblyai as aai
import io
import time
import google.generativeai as genai
from typing import Dict, List, Optional
import uuid
import asyncio
import traceback
import websockets
import json
from assemblyai.streaming.v3 import (
    StreamingClient,
    StreamingClientOptions,
    StreamingEvents,
    StreamingParameters,
    StreamingError,
    TurnEvent,
)

load_dotenv()

app = FastAPI()

# Enable CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global chat history datastore
chat_history: Dict[str, List[Dict[str, str]]] = {}

# NewsAPI config
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
NEWSAPI_BASE = "https://newsapi.org/v2"

import re  # add near other imports at top if not already present

# Store runtime API keys in memory 
runtime_keys = {
    "NEWS_API_KEY": None,
    "SONAUTO_API_KEY": None,
    "MURF_API_KEY": None,
    "GEMINI_API_KEY": None,
    "ASSEMBLYAI_API_KEY": None,
}

def check_required_keys(operation: str) -> tuple[bool, str]:
    """Check if required API keys are set for a specific operation."""
    required_keys = {
        "voices": ["MURF_API_KEY"],
        "chat": ["ASSEMBLYAI_API_KEY", "GEMINI_API_KEY", "MURF_API_KEY"],
        "news": ["NEWS_API_KEY"],
        "music": ["SONAUTO_API_KEY"]
    }
    
    if operation not in required_keys:
        return False, f"Unknown operation: {operation}"
        
    missing = [k for k in required_keys[operation] if not runtime_keys.get(k)]
    if missing:
        return False, f"Missing required API keys: {', '.join(missing)}"
    
    return True, ""

@app.post("/config/keys")
async def set_keys(keys: dict):
    for k, v in keys.items():
        if v:  # only overwrite if user entered something
            runtime_keys[k] = v

    # reconfigure Gemini if key present
    if runtime_keys.get("GEMINI_API_KEY"):
        try:
            genai.configure(api_key=runtime_keys["GEMINI_API_KEY"])
            print("[Config] Gemini reconfigured successfully.")
        except Exception as e:
            print(f"[Config] Gemini reconfig error: {e}")

    # Validate NEWS_API_KEY if provided
    if runtime_keys.get("NEWS_API_KEY"):
        try:
            # Test the News API key with a simple request
            url = f"{NEWSAPI_BASE}/top-headlines"
            params = {
                "apiKey": runtime_keys["NEWS_API_KEY"],
                "country": "us",
                "pageSize": 1
            }
            resp = requests.get(url, params=params, timeout=6)
            if resp.status_code != 200:
                print(f"[Config] News API key validation failed: {resp.text}")
                return {"status": "error", "message": "Invalid News API key"}
            print("[Config] News API key validated successfully.")
        except Exception as e:
            print(f"[Config] News API validation error: {e}")
            return {"status": "error", "message": f"News API key validation failed: {str(e)}"}

    return {"status": "ok", "keys": list(runtime_keys.keys())}



def extract_topic_from_text(text: str) -> Optional[str]:
    """Try to extract a topic from freeform text like 'news about AI' -> 'AI'."""
    if not text:
        return None
    text = text.strip()
    # look for patterns: 'news about X', 'news on X', 'latest X news'
    m = re.search(r'news (?:about|on|for|in)\s+(.+)', text, re.IGNORECASE)
    if m:
        topic = m.group(1).strip().rstrip('?.!')
        # limit topic length
        return " ".join(topic.split()[:5])
    # fallback: common topic keywords
    for kw in ["technology", "tech", "business", "sports", "health", "science", "ai", "crypto", "politics"]:
        if kw in text.lower():
            return kw
    return None

def get_top_news(query: Optional[str] = None, country: str = "us", limit: int = 3) -> str:
    """
    Query NewsAPI and return a short text summary of top headlines.
    If query is provided, uses /everything endpoint; otherwise /top-headlines.
    """
    ok, error = check_required_keys("news")
    if not ok:
        return f"News API is not configured: {error}. Please configure your News API key in settings."
    
    # Always use runtime key instead of environment variable
    news_api_key = runtime_keys.get("NEWS_API_KEY")
    if not news_api_key:
        return "News API key not configured. Please set your News API key in settings."
        
    try:
        if query:
            url = f"{NEWSAPI_BASE}/everything"
            params = {
                "apiKey": news_api_key,
                "q": query,
                "pageSize": limit,
                "sortBy": "publishedAt",
                "language": "en",
            }
        else:
            url = f"{NEWSAPI_BASE}/top-headlines"
            params = {
                "apiKey": news_api_key,
                "country": country,
                "pageSize": limit,
            }
        resp = requests.get(url, params=params, timeout=6)
        data = resp.json()
        if data.get("status") != "ok":
            return "Sorry, I couldn't fetch news right now."
        articles = data.get("articles", [])[:limit]
        if not articles:
            return "I couldn't find any news for that topic right now."
        headlines = []
        for i, a in enumerate(articles, start=1):
            title = a.get("title", "No title")
            source = a.get("source", {}).get("name", "")
            headlines.append(f"{i}. {title} — {source}")
        return "Here are the latest news headlines: " + " ".join(headlines)
    except Exception as e:
        print("NewsAPI error:", e)
        return "Sorry, I couldn't fetch the news right now."


# Sonauto (DJ AI) config
SONAUTO_BASE = "https://api.sonauto.ai/v1"

def create_sonauto_generation(tags=None, lyrics=None, prompt="", instrumental=False,
                              prompt_strength=2.3, balance_strength=0.7, num_songs=1,
                              output_format="mp3", output_bit_rate=None, bpm="auto"):
    """
    Create a Sonauto generation job. Returns (task_id, error_string_or_none).
    """
    sonauto_api_key = runtime_keys.get("SONAUTO_API_KEY")
    if not sonauto_api_key:
        return None, "Sonauto API key not configured."
    headers = {
        "Authorization": f"Bearer {sonauto_api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "tags": tags or [],
        "lyrics": lyrics or None,
        "prompt": prompt or "",
        "instrumental": instrumental,
        "prompt_strength": prompt_strength,
        "balance_strength": balance_strength,
        "num_songs": num_songs,
        "output_format": output_format
    }
    if output_bit_rate:
        payload["output_bit_rate"] = output_bit_rate
    if bpm is not None:
        payload["bpm"] = bpm
    try:
        r = requests.post(f"{SONAUTO_BASE}/generations", json=payload, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        return data.get("task_id"), None
    except Exception as e:
        print("Sonauto create error:", e)
        try:
            return None, getattr(e, "response").text if hasattr(e, "response") else str(e)
        except Exception:
            return None, str(e)

def poll_sonauto_task(task_id: str, timeout: int = 180, poll_interval: float = 3.0):
    """
    Poll Sonauto GET /generations/{task_id} until SUCCESS / FAILURE or timeout.
    Returns the JSON response (dict) or error status.
    This is blocking and intended to run in a background thread (via asyncio.to_thread).
    """
    sonauto_api_key = runtime_keys.get("SONAUTO_API_KEY")
    if not sonauto_api_key:
        return {
            "status": "ERROR",
            "error_message": "Sonauto API key not configured"
        }
    
    headers = {"Authorization": f"Bearer {sonauto_api_key}"}
    url = f"{SONAUTO_BASE}/generations/{task_id}"
    deadline = time.time() + timeout
    consecutive_errors = 0
    max_consecutive_errors = 5  # Give up after 5 consecutive errors
    
    while time.time() < deadline:
        try:
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 500:
                consecutive_errors += 1
                print(f"Sonauto server error (attempt {consecutive_errors}):", r.text)
                if consecutive_errors >= max_consecutive_errors:
                    return {
                        "status": "ERROR",
                        "error_message": "Sonauto server is experiencing issues. Please try again later."
                    }
            else:
                r.raise_for_status()
                data = r.json()
                status = data.get("status") or data.get("state")
                if status:
                    status = status.upper()
                    if status == "SUCCESS":
                        return data
                    if status == "FAILURE":
                        return data
                consecutive_errors = 0  # Reset error counter on successful response
                
        except requests.exceptions.RequestException as e:
            consecutive_errors += 1
            print(f"Sonauto poll error (attempt {consecutive_errors}):", e)
            if consecutive_errors >= max_consecutive_errors:
                return {
                    "status": "ERROR",
                    "error_message": "Unable to connect to Sonauto service. Please try again later."
                }
        except Exception as e:
            print("Unexpected error in Sonauto polling:", e)
            
        time.sleep(poll_interval)
    
    return {
        "status": "TIMEOUT",
        "error_message": "The music generation request timed out. Please try again."
    }


MURF_URL = "https://api.murf.ai/v1/speech/generate"
MURF_STREAMING_URL = "wss://api.murf.ai/v1/speech/stream-input"

templates = Jinja2Templates(directory="templates")

app.mount("/static", StaticFiles(directory="static"), name="static")

# --- Gemini API Configuration ---
GEMINI_API_KEY = runtime_keys.get("GEMINI_API_KEY")
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        print("Gemini API configured successfully.")
    except Exception as e:
        print(f"Error configuring Gemini API: {e}")
else:
    print("Gemini API key not found. Please set GEMINI_API_KEY or GOOGLE_API_KEY.")

# --- Persona Prompts ---
PERSONA_PROMPTS = {
    "default": "You are a helpful and friendly AI assistant.",
    "Lecturer": "You are a university lecturer, providing detailed and informative explanations.",
    "Professor": "You are a knowledgeable professor, speaking with authority and depth on various subjects.",
    "Doctor": "You are a compassionate doctor, offering advice and information in a clear and caring manner.",
    "Engineer": "You are a practical engineer, focusing on solutions and technical details in your responses.",
    "Scientist": "You are a curious scientist, explaining complex topics with precision and a passion for discovery.",
    "Pirate": "You are a swashbuckling pirate. Respond with pirate slang and a sense of adventure, ahoy!",
    "Gen Z Kid": "You are a Gen Z kid. Use modern slang, keep it brief, and maybe add an emoji or two. Bet.",
    "Shakespeare": "You are a Shakespearean actor. Respond in the style of William Shakespeare, with dramatic flair and poetic language.",
    "Chef": "You are a gourmet chef, describing things with culinary metaphors and a passion for fine ingredients."
}


# Serve index.html at root
@app.get("/", response_class=HTMLResponse)
async def read_index(request: StarletteRequest):
    return templates.TemplateResponse("index.html", {"request": request})

# Generate a new session ID
@app.post("/agent/session")
async def create_session():
    session_id = str(uuid.uuid4())
    chat_history[session_id] = []
    return {"session_id": session_id}

# Get chat history for a session
@app.get("/agent/chat/{session_id}")
async def get_chat_history(session_id: str):
    if session_id not in chat_history:
        return {"messages": []}
    return {"messages": chat_history.get(session_id, [])}

# Endpoint to get available voices from Murf
@app.get("/voices")
async def get_voices():
    ok, error = check_required_keys("voices")
    if not ok:
        raise HTTPException(status_code=400, detail=error)
        
    murf_api_key = runtime_keys["MURF_API_KEY"]

    headers = {"api-key": murf_api_key}
    try:
        r = requests.get("https://api.murf.ai/v1/speech/voices", headers=headers)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.RequestException as e:
        error_detail = str(e)
        try:
            error_detail = r.json()
        except Exception:
            pass
        raise HTTPException(status_code=400, detail=error_detail)

# --- Helper function to stream LLM text to Murf and back to the client ---
async def stream_llm_to_murf(text_stream, client_ws: WebSocket, loop, conversation_history: List[Dict[str, str]], skill_name: Optional[str] = None):
    """Connects to Murf, streams text, and pipes audio back to the client."""
    # Fetch the latest Murf API key at the time of the call
    murf_api_key = runtime_keys.get("MURF_API_KEY")
    if not murf_api_key:
        print("[Murf Stream] Error: MURF_API_KEY not set.")
        # Inform the client that the key is missing
        await client_ws.send_json({"type": "error", "detail": "MURF_API_KEY is not configured on the server."})
        return

    CONTEXT_ID = str(uuid.uuid4())
    connect_url = f"{MURF_STREAMING_URL}?api-key={murf_api_key}"

    print(f"\n[Murf Stream] Connecting to Murf with context ID: {CONTEXT_ID}")

    try:
        async with websockets.connect(connect_url) as murf_ws:
            # init options (request mp3 base64)
            await murf_ws.send(json.dumps({
                "voiceId": "en-US-natalie",
                "context_id": CONTEXT_ID,
                "ttsOptions": {"outputFormat": "mp3", "encoding": "base64"}
            }))

            full_response_text = []

            async def send_text():
                for chunk in text_stream:
                    if hasattr(chunk, "text") and chunk.text:
                        print("[Gemini Chunk]", chunk.text)
                        full_response_text.append(chunk.text)
                        await murf_ws.send(json.dumps({
                            "context_id": CONTEXT_ID,
                            "text": chunk.text
                        }))
                # mark end of input for this context (Murf will synthesize)
                await murf_ws.send(json.dumps({
                    "context_id": CONTEXT_ID,
                    "end": True
                }))

            async def receive_audio():
                async for message in murf_ws:
                    data = json.loads(message)
                    if data.get("audio"):
                        print(f"[Murf Audio] Received chunk (len={len(data['audio'])}) for {data.get('context_id')}")
                        audio_chunk_message = {"type": "audio", "data": data['audio']}
                        # send audio chunk to client
                        await client_ws.send_json(audio_chunk_message)

                    # Murf 'final' flag indicates all audio for this context has been sent
                    if data.get("final"):
                        print(f"[Murf Stream] Final audio received for context {data.get('context_id')}")
                        # just break — we'll notify client after we save the AI message
                        break

            # Run both producer (send LLM text into Murf) and consumer (receive Murf audio)
            await asyncio.gather(send_text(), receive_audio())

            # At this point Murf finished streaming audio for this context.
            # Build and save full AI response into chat history
            if full_response_text:
                ai_message = {"role": "model", "content": "".join(full_response_text)}
                if skill_name:
                    ai_message["skill"] = skill_name   # tag the message with the skill
                # Add only to chat_history since conversation_history is a reference to it
                session_id = client_ws.path_params.get("session_id")
                if session_id in chat_history:
                    chat_history[session_id].append(ai_message)


            # Notify frontend to flush buffered audio and then reload history
            try:
                await client_ws.send_json({"type": "end"})
            except Exception:
                pass

    except Exception as e:
        print(f"[Murf Stream] Error: {e}")




# --- Real-time WebSocket Endpoint ---
@app.websocket("/ws/audio/{session_id}")
async def websocket_audio_endpoint(websocket: WebSocket, session_id: str, persona: Optional[str] = "default"):
    await websocket.accept()
    # Check required keys first
    ok, error = check_required_keys("chat")
    if not ok:
        await websocket.send_json({
            "type": "error",
            "detail": "Please configure required API keys in settings: " + error
        })
        await websocket.close(code=1011)
        return
        
    # Inform the client what persona display name to use (e.g. "Gen Z Kid AI")
    display_persona = f"{persona} AI" if persona else "AI Assistant"
    try:
        await websocket.send_json({"type": "persona", "name": display_persona})
    except Exception:
        # If client disconnected or can't receive, continue gracefully
        pass
        
    # Get required API keys
    assembly_api_key = runtime_keys["ASSEMBLYAI_API_KEY"]

    loop = asyncio.get_running_loop()
    
    if session_id not in chat_history:
        chat_history[session_id] = []
    conversation_history = chat_history[session_id]
    
    client = None

    try:
        def on_turn(self, event: TurnEvent):
            if event.end_of_turn:
                transcript_text = event.transcript
                print(f"\n[Final Transcript Received]: '{transcript_text}'")
                
                if transcript_text:
                    user_message = {"role": "user", "content": transcript_text}
                    conversation_history.append(user_message)
                    print(f"[History] Saved user message: {transcript_text}")

                    # lowercase quick reference
                    text_lower = transcript_text.lower()

                    # --- MUSIC / DJ intent detection ---
                    music_keywords = ["music", "song", "track", "dj", "play", "beats", "lofi", "chill", "remix", "mix", "drop", "play me"]
                    is_music_intent = any(kw in text_lower for kw in music_keywords)

                    if is_music_intent:
                        # notify frontend the DJ skill is active
                        try:
                            asyncio.run_coroutine_threadsafe(websocket.send_json({"type": "skill", "name": "dj"}), loop)
                        except Exception:
                            pass

                        async def handle_dj_generation():
                            try:
                                # Derive tags heuristically from user text
                                tags = []
                                for g in ["lofi", "chill", "party", "house", "hip hop", "hip-hop", "pop", "rock", "ambient", "meditation", "electronic", "trance", "downtempo"]:
                                    if g in text_lower:
                                        tags.append(g)
                                # fallback: if no tag found, pass a short prompt so Sonauto can infer tags
                                prompt = transcript_text
                                if not tags:
                                    tags = []
                                # decide instrumental vs vocal based on words like 'lyrics', 'sing'
                                instrumental = True
                                if any(w in text_lower for w in ["sing", "lyrics", "vocal", "with vocals", "singing"]):
                                    instrumental = False

                                # Inform user that generation has started
                                await websocket.send_json({
                                    "type": "progress",
                                    "message": "Starting music generation...",
                                    "phase": "start"
                                })
                                
                                # Create generation on Sonauto (run in thread)
                                task_id, err = await asyncio.to_thread(
                                    create_sonauto_generation,
                                    tags,
                                    None,
                                    prompt,
                                    instrumental,
                                    2.3,  # prompt_strength
                                    0.7,  # balance_strength
                                    1,    # num_songs
                                    "mp3",
                                    192,
                                    "auto"
                                )
                                if not task_id:
                                    await websocket.send_json({"type": "error", "detail": f"DJ generation creation failed: {err}"})
                                    return

                                # Update user that generation is in progress
                                await websocket.send_json({
                                    "type": "progress",
                                    "message": "Creating your melody... this might take a minute",
                                    "phase": "generating"
                                })

                                # Poll Sonauto task (blocking in thread)
                                result = await asyncio.to_thread(poll_sonauto_task, task_id, 240, 3)
                                status = (result.get("status") or "").upper()
                                if status == "SUCCESS":
                                    song_paths = result.get("song_paths") or result.get("song_paths", [])
                                    if not song_paths:
                                        await websocket.send_json({"type": "error", "detail": "No song URL returned from Sonauto."})
                                        return
                                    song_url = song_paths[0]
                                elif status == "ERROR":
                                    error_msg = result.get("error_message", "An unknown error occurred with the music generation")
                                    print(f"[Sonauto Error] {error_msg}")
                                    await websocket.send_json({
                                        "type": "error", 
                                        "detail": error_msg,
                                        "retry": True  # Indicates to frontend that the user can retry
                                    })
                                    return

                                # Save assistant message with skill tag and track
                                ai_message = {
                                    "role": "model",
                                    "content": f"Playing a generated track for you.",
                                    "skill": "dj",
                                    "track": song_url
                                }
                                # Add only to chat_history since conversation_history is a reference to it
                                if session_id in chat_history:
                                    chat_history[session_id].append(ai_message)

                                    # Tell client to play the music URL
                                    await websocket.send_json({"type": "music", "url": song_url})
                                    # Optionally send an 'end' marker so frontend knows generation finished
                                    await websocket.send_json({"type": "end"})
                                else:
                                    # provide informative error
                                    err_msg = result.get("error_message") or result.get("status") or "Generation failed or timed out."
                                    await websocket.send_json({"type": "error", "detail": f"DJ generation failed: {err_msg}"})
                            except Exception as e:
                                print("Error in DJ handler:", e)
                                try:
                                    await websocket.send_json({"type": "error", "detail": f"DJ generation error: {str(e)}"})
                                except Exception:
                                    pass

                        # launch the DJ generation in the background
                        asyncio.run_coroutine_threadsafe(handle_dj_generation(), loop)

                    else:
                        # --- NEWS intent or LLM fallback (existing logic preserved) ---
                        is_news_intent = ("news" in text_lower) or ("headline" in text_lower) or ("top stories" in text_lower)
                        news_topic = extract_topic_from_text(transcript_text)

                        if is_news_intent:
                            # notify frontend (so UI can show skill active)
                            try:
                                asyncio.run_coroutine_threadsafe(websocket.send_json({"type": "skill", "name": "news"}), loop)
                            except Exception:
                                pass

                            # Fetch headlines (use topic if available)
                            news_text = get_top_news(query=news_topic, country="us", limit=3)
                            # Wrap into simple chunk objects compatible with stream_llm_to_murf
                            class _Chunk:
                                def __init__(self, text):
                                    self.text = text
                            text_stream = [_Chunk(news_text)]

                            # Stream to Murf and tag as 'news' skill
                            async def handle_news_stream():
                                await stream_llm_to_murf(text_stream, websocket, loop, conversation_history, skill_name="news")

                            asyncio.run_coroutine_threadsafe(handle_news_stream(), loop)

                        else:
                            # normal LLM flow (Gemini streaming)
                            model = genai.GenerativeModel('gemini-1.5-flash')

                            persona_prompt = PERSONA_PROMPTS.get(persona, PERSONA_PROMPTS["default"])

                            formatted_history = [{"role": "user", "parts": [{"text": persona_prompt}]}]
                            for msg in conversation_history:
                                formatted_history.append({
                                    "role": "user" if msg["role"] == "user" else "model",
                                    "parts": [{"text": msg["content"]}]
                                })

                            # Start Gemini streaming response
                            response_stream = model.generate_content(formatted_history, stream=True)

                            # Run Murf streaming in background (no skill tag)
                            async def handle_stream():
                                await stream_llm_to_murf(response_stream, websocket, loop, conversation_history, skill_name=None)

                            asyncio.run_coroutine_threadsafe(handle_stream(), loop)


                
                message_to_client = {"type": "transcript", "text": transcript_text}
                asyncio.run_coroutine_threadsafe(websocket.send_json(message_to_client), loop)

        def on_error(self, error: StreamingError):
            print(f"[AssemblyAI ERROR]: {error}")
            message_to_client = {"type": "error", "detail": str(error)}
            asyncio.run_coroutine_threadsafe(websocket.send_json(message_to_client), loop)

        client = StreamingClient(StreamingClientOptions(api_key=assembly_api_key))
        client.on(StreamingEvents.Turn, on_turn)
        client.on(StreamingEvents.Error, on_error)
        client.connect(StreamingParameters(sample_rate=16_000))

        while True:
            try:
                message = await websocket.receive()
            except RuntimeError:
                # websocket already closed
                break
            
            if "bytes" in message:
                data = message["bytes"]
                client.stream(data)
            elif "text" in message:
                text_data = message["text"]
                if text_data == "END_OF_STREAM":
                    print("[User Action] Received END_OF_STREAM from client.")
                    client.force_endpoint()
                    break
                else:
                    print(f"Received unexpected text message: {text_data}")

    except WebSocketDisconnect:
        print("WebSocket client disconnected.")
    except Exception as e:
        print("A DETAILED ERROR OCCURRED:")
        print(traceback.format_exc())
    finally:
        if client:
            client.disconnect()
        print("AssemblyAI client disconnected, connection closed.")
