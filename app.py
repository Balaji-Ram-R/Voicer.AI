

import os
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request as StarletteRequest
import assemblyai as aai
import io
import time
from assemblyai import Client, Transcriber, types
from pydantic import BaseModel
import google.generativeai as genai
from typing import Dict, List, Optional
import uuid

load_dotenv()

app = FastAPI()

# Enable CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Replace with your frontend origin in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Chat history datastore - in-memory dictionary for prototype
# In production, use a proper database like PostgreSQL, MongoDB, or Redis
chat_history: Dict[str, List[Dict[str, str]]] = {}

API_KEY = os.getenv("MURF_API_KEY")
MURF_URL = "https://api.murf.ai/v1/speech/generate"

templates = Jinja2Templates(directory="templates")

app.mount("/static", StaticFiles(directory="static"), name="static")

# Serve index.html at root
@app.get("/", response_class=HTMLResponse)
async def read_index(request: StarletteRequest):
    return templates.TemplateResponse("index.html", {"request": request})

# Generate a new session ID
@app.post("/agent/session")
async def create_session():
    """Create a new chat session and return the session ID"""
    session_id = str(uuid.uuid4())
    chat_history[session_id] = []
    return {"session_id": session_id}

# Get chat history for a session
@app.get("/agent/chat/{session_id}")
async def get_chat_history(session_id: str):
    """Get chat history for a specific session"""
    if session_id not in chat_history:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"messages": chat_history[session_id]}

# Endpoint to receive uploaded audio file
@app.post("/upload_audio")
async def upload_audio(file: UploadFile = File(...)):
    upload_dir = "uploads"
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, file.filename)
    # Save file
    with open(file_path, "wb") as f:
        content = await file.read()
        f.write(content)
    return {
        "filename": file.filename,
        "content_type": file.content_type,
        "size": len(content)
    }

# Endpoint to get available voices from Murf
@app.get("/voices")
async def get_voices():
    headers = {
        "api-key": API_KEY
    }
    try:
        r = requests.get("https://api.murf.ai/v1/speech/voices", headers=headers)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.RequestException as e:
        try:
            error_detail = r.json()
        except Exception:
            error_detail = str(e)
        raise HTTPException(status_code=400, detail=error_detail)

# Accept voice_id from frontend
@app.post("/generate")
async def generate_audio(request: Request):
    form = await request.form()
    text = form.get("text")
    voice_id = form.get("voice_id", "en-US-natalie")
    headers = {
        "api-key": API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "text": text,
        "voice_id": voice_id,
        "output_format": "mp3"
    }
    try:
        r = requests.post(MURF_URL, json=payload, headers=headers)
        r.raise_for_status()
        response = r.json()
        audio_url = response.get("audio_url") or response.get("audioFile")
        if not audio_url:
            print("Murf API response:", response)
            raise HTTPException(status_code=500, detail=f"No audio URL returned. Murf response: {response}")
        return {"audio_url": audio_url}
    except requests.exceptions.RequestException as e:
        try:
            error_detail = r.json()
        except Exception:
            error_detail = str(e)
        raise HTTPException(status_code=400, detail=error_detail)

ASSEMBLYAI_API_KEY = os.getenv("ASSEMBLYAI_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

# Configure Gemini if key present
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
    except Exception:
        # Defer detailed error handling to endpoint call
        pass


# Endpoint for the full non-streaming pipeline
@app.post("/llm/query")
async def llm_query_audio(
    file: UploadFile = File(...),
    model: str = Form("gemini-1.5-flash"),
    voice_id: str = Form("en-US-natalie")
):
    """
    Full non-streaming pipeline: Audio → Transcription → LLM → Murf TTS → Return audio
    """
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="Gemini API key not set. Define GEMINI_API_KEY or GOOGLE_API_KEY in environment or .env")
    
    if not API_KEY:
        raise HTTPException(status_code=500, detail="MURF API key not set")
    
    try:
        # 1. Read audio file
        audio_bytes = await file.read()
        
        # 2. Transcribe audio with AssemblyAI
        transcript_text = _transcribe_with_assemblyai(audio_bytes)
        
        if not transcript_text or transcript_text.strip() == "":
            raise HTTPException(status_code=400, detail="Could not transcribe audio - no text detected")
        
        # 3. Send transcript to LLM
        try:
            genai_model = genai.GenerativeModel(model)
            result = genai_model.generate_content(transcript_text)
            response_text = getattr(result, "text", None)
            if not response_text:
                try:
                    response_text = result.candidates[0].content.parts[0].text
                except Exception:
                    response_text = str(result)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"LLM error: {str(e)}")
        
        # 4. Handle Murf API character limit (3000 chars)
        if len(response_text) > 3000:
            # Truncate to 3000 characters, trying to end at a sentence boundary
            truncated_text = response_text[:3000]
            last_period = truncated_text.rfind('.')
            last_exclamation = truncated_text.rfind('!')
            last_question = truncated_text.rfind('?')
            
            # Find the last sentence ending
            last_sentence_end = max(last_period, last_exclamation, last_question)
            if last_sentence_end > 2800:  # Leave some buffer
                truncated_text = truncated_text[:last_sentence_end + 1]
            else:
                # If no good sentence boundary, just truncate and add ellipsis
                truncated_text = truncated_text[:2997] + "..."
            
            response_text = truncated_text
        
        # 5. Generate TTS with Murf
        headers = {
            "api-key": API_KEY,
            "Content-Type": "application/json"
        }
        payload = {
            "text": response_text,
            "voice_id": voice_id,
            "output_format": "mp3"
        }
        
        r = requests.post(MURF_URL, json=payload, headers=headers)
        r.raise_for_status()
        response = r.json()
        audio_url = response.get("audio_url") or response.get("audioFile")
        
        if not audio_url:
            raise HTTPException(status_code=500, detail=f"No audio URL returned. Murf response: {response}")
        
        return {
            "audio_url": audio_url,
            "transcript": transcript_text,
            "llm_response": response_text,
            "model": model,
            "voice_id": voice_id
        }
        
    except requests.exceptions.RequestException as e:
        try:
            error_detail = r.json()
        except Exception:
            error_detail = str(e)
        raise HTTPException(status_code=400, detail=error_detail)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/transcribe/file")
async def transcribe_file(file: UploadFile = File(...)):
    if not ASSEMBLYAI_API_KEY:
        raise HTTPException(status_code=500, detail="AssemblyAI API key not set.")
    try:
        audio_bytes = await file.read()
        headers = {
            "authorization": ASSEMBLYAI_API_KEY,
        }
        # 1. Upload audio to AssemblyAI
        upload_response = requests.post(
            "https://api.assemblyai.com/v2/upload",
            headers=headers,
            data=audio_bytes
        )
        if upload_response.status_code != 200:
            raise RuntimeError(f"Upload failed: {upload_response.text}")
        audio_url = upload_response.json()["upload_url"]

        # 2. Request transcription
        transcript_response = requests.post(
            "https://api.assemblyai.com/v2/transcript",
            headers={**headers, "content-type": "application/json"},
            json={"audio_url": audio_url}
        )
        if transcript_response.status_code != 200:
            raise RuntimeError(f"Transcript request failed: {transcript_response.text}")
        transcript_id = transcript_response.json()["id"]

        # 3. Poll for completion
        polling_url = f"https://api.assemblyai.com/v2/transcript/{transcript_id}"
        while True:
            poll_response = requests.get(polling_url, headers=headers)
            if poll_response.status_code != 200:
                raise RuntimeError(f"Polling failed: {poll_response.text}")
            status = poll_response.json()["status"]
            if status == "completed":
                return {"transcript": poll_response.json()["text"]}
            elif status == "error":
                raise RuntimeError(f"Transcription failed: {poll_response.json().get('error')}")
            time.sleep(2)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Helper to transcribe raw audio bytes with AssemblyAI
def _transcribe_with_assemblyai(audio_bytes: bytes) -> str:
    if not ASSEMBLYAI_API_KEY:
        raise HTTPException(status_code=500, detail="AssemblyAI API key not set.")
    headers = {
        "authorization": ASSEMBLYAI_API_KEY,
    }
    # 1. Upload audio to AssemblyAI
    upload_response = requests.post(
        "https://api.assemblyai.com/v2/upload",
        headers=headers,
        data=audio_bytes,
    )
    if upload_response.status_code != 200:
        raise RuntimeError(f"Upload failed: {upload_response.text}")
    audio_url = upload_response.json()["upload_url"]

    # 2. Request transcription
    transcript_response = requests.post(
        "https://api.assemblyai.com/v2/transcript",
        headers={**headers, "content-type": "application/json"},
        json={"audio_url": audio_url},
    )
    if transcript_response.status_code != 200:
        raise RuntimeError(f"Transcript request failed: {transcript_response.text}")
    transcript_id = transcript_response.json()["id"]

    # 3. Poll for completion
    polling_url = f"https://api.assemblyai.com/v2/transcript/{transcript_id}"
    while True:
        poll_response = requests.get(polling_url, headers=headers)
        if poll_response.status_code != 200:
            raise RuntimeError(f"Polling failed: {poll_response.text}")
        status = poll_response.json()["status"]
        if status == "completed":
            return poll_response.json().get("text", "")
        elif status == "error":
            raise RuntimeError(
                f"Transcription failed: {poll_response.json().get('error')}"
            )
        time.sleep(2)


# New endpoint: accepts audio, transcribes it, sends text to Murf, returns Murf audio URL
@app.post("/tts/echo")
async def tts_echo(file: UploadFile = File(...), voice_id: str = Form("en-US-natalie")):
    try:
        audio_bytes = await file.read()

        # 1) Transcribe with AssemblyAI
        transcript_text = _transcribe_with_assemblyai(audio_bytes)

        # 2) Generate TTS with Murf
        headers = {
            "api-key": API_KEY,
            "Content-Type": "application/json",
        }
        payload = {
            "text": transcript_text or "",
            "voice_id": voice_id,
            "output_format": "mp3",
        }
        r = requests.post(MURF_URL, json=payload, headers=headers)
        r.raise_for_status()
        response = r.json()
        audio_url = response.get("audio_url") or response.get("audioFile")
        if not audio_url:
            raise HTTPException(
                status_code=500,
                detail=f"No audio URL returned. Murf response: {response}",
            )

        return {"audio_url": audio_url, "transcript": transcript_text}
    except requests.exceptions.RequestException as e:
        try:
            error_detail = r.json()  # type: ignore[name-defined]
        except Exception:
            error_detail = str(e)
        raise HTTPException(status_code=400, detail=error_detail)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# New endpoint for chat with history
@app.post("/agent/chat/{session_id}")
async def agent_chat(
    session_id: str,
    file: UploadFile = File(...),
    model: str = Form("gemini-1.5-flash"),
    voice_id: str = Form("en-US-natalie")
):
    """
    Full conversational pipeline with chat history: 
    Audio → Transcription → Append to history → LLM with context → Add response to history → TTS → Return audio
    """
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="Gemini API key not set. Define GEMINI_API_KEY or GOOGLE_API_KEY in environment or .env")
    
    if not API_KEY:
        raise HTTPException(status_code=500, detail="MURF API key not set")
    
    # Initialize session if it doesn't exist
    if session_id not in chat_history:
        chat_history[session_id] = []
    
    try:
        # 1. Read audio file
        audio_bytes = await file.read()
        
        # 2. Transcribe audio with AssemblyAI
        transcript_text = _transcribe_with_assemblyai(audio_bytes)
        
        if not transcript_text or transcript_text.strip() == "":
            raise HTTPException(status_code=400, detail="Could not transcribe audio - no text detected")
        
        # 3. Add user message to chat history
        user_message = {"role": "user", "content": transcript_text, "timestamp": time.time()}
        chat_history[session_id].append(user_message)
        
        # 4. Prepare conversation context for LLM
        conversation_context = ""
        if len(chat_history[session_id]) > 1:
            # Include previous messages for context (limit to last 10 messages to avoid token limits)
            recent_messages = chat_history[session_id][-10:]
            conversation_context = "Previous conversation:\n"
            for msg in recent_messages[:-1]:  # Exclude the current user message
                role = "User" if msg["role"] == "user" else "Assistant"
                conversation_context += f"{role}: {msg['content']}\n"
            conversation_context += "\nCurrent user message: "
        
        # 5. Send transcript to LLM with conversation context
        try:
            genai_model = genai.GenerativeModel(model)
            
            # Create the prompt with conversation context
            if conversation_context:
                full_prompt = conversation_context + transcript_text
            else:
                full_prompt = transcript_text
            
            result = genai_model.generate_content(full_prompt)
            response_text = getattr(result, "text", None)
            if not response_text:
                try:
                    response_text = result.candidates[0].content.parts[0].text
                except Exception:
                    response_text = str(result)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"LLM error: {str(e)}")
        
        # 6. Add AI response to chat history
        ai_message = {"role": "assistant", "content": response_text, "timestamp": time.time()}
        chat_history[session_id].append(ai_message)
        
        # 7. Handle Murf API character limit (3000 chars)
        if len(response_text) > 3000:
            # Truncate to 3000 characters, trying to end at a sentence boundary
            truncated_text = response_text[:3000]
            last_period = truncated_text.rfind('.')
            last_exclamation = truncated_text.rfind('!')
            last_question = truncated_text.rfind('?')
            
            # Find the last sentence ending
            last_sentence_end = max(last_period, last_exclamation, last_question)
            if last_sentence_end > 2800:  # Leave some buffer
                truncated_text = truncated_text[:last_sentence_end + 1]
            else:
                # If no good sentence boundary, just truncate and add ellipsis
                truncated_text = truncated_text[:2997] + "..."
            
            response_text = truncated_text
        
        # 8. Generate TTS with Murf
        headers = {
            "api-key": API_KEY,
            "Content-Type": "application/json"
        }
        payload = {
            "text": response_text,
            "voice_id": voice_id,
            "output_format": "mp3"
        }
        
        r = requests.post(MURF_URL, json=payload, headers=headers)
        r.raise_for_status()
        response = r.json()
        audio_url = response.get("audio_url") or response.get("audioFile")
        
        if not audio_url:
            raise HTTPException(status_code=500, detail=f"No audio URL returned. Murf response: {response}")
        
        return {
            "audio_url": audio_url,
            "transcript": transcript_text,
            "llm_response": response_text,
            "model": model,
            "voice_id": voice_id,
            "session_id": session_id,
            "message_count": len(chat_history[session_id])
        }
        
    except requests.exceptions.RequestException as e:
        try:
            error_detail = r.json()
        except Exception:
            error_detail = str(e)
        raise HTTPException(status_code=400, detail=error_detail)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))