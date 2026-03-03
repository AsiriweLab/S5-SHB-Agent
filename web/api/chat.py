"""
Chat / AI generation endpoint.

Provides a Gemini-backed chat endpoint for AI-powered features
in the threat and home builders (threat generation, analysis, etc.).
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel

router = APIRouter()

# Gemini model (lazy init)
_gemini_model = None


def _get_gemini():
    """Lazily initialize and return the Gemini model."""
    global _gemini_model
    if _gemini_model is not None:
        return _gemini_model

    try:
        import google.generativeai as genai

        # Load API key from engine config or environment
        api_key = os.environ.get("GOOGLE_API_KEY", "")
        if not api_key:
            # Try loading from engine/.env
            env_path = os.path.join(
                os.path.dirname(__file__), "..", "..", "engine", ".env"
            )
            if os.path.exists(env_path):
                with open(env_path) as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("GOOGLE_API_KEY="):
                            api_key = line.split("=", 1)[1].strip()
                            break

        if not api_key:
            logger.warning("GOOGLE_API_KEY not found — AI features disabled")
            return None

        model_name = os.environ.get("DEFAULT_MODEL", "gemini-2.0-flash")
        genai.configure(api_key=api_key)
        _gemini_model = genai.GenerativeModel(model_name)
        logger.info(f"Gemini model initialized: {model_name}")
        return _gemini_model
    except Exception as e:
        logger.warning(f"Failed to initialize Gemini: {e}")
        return None


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str
    session_id: str = ""
    use_rag: bool = False
    temperature: float = 0.7
    max_tokens: int = 2048


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/health")
async def chat_health() -> dict:
    """Check if the AI (Gemini) service is available."""
    model = _get_gemini()
    available = model is not None
    return {
        "ollama_available": available,  # kept for frontend compat
        "gemini_available": available,
        "provider": "gemini",
    }


@router.post("/")
async def chat(body: ChatRequest) -> dict:
    """Send a message to Gemini and get a response."""
    model = _get_gemini()
    if model is None:
        raise HTTPException(
            status_code=503,
            detail="AI service unavailable. GOOGLE_API_KEY not configured.",
        )

    try:
        generation_config = {
            "temperature": body.temperature,
            "max_output_tokens": body.max_tokens,
        }
        response = model.generate_content(
            body.message,
            generation_config=generation_config,
        )
        return {
            "message": response.text,
            "session_id": body.session_id,
            "provider": "gemini",
        }
    except Exception as e:
        logger.error(f"Gemini generation failed: {e}")
        raise HTTPException(status_code=500, detail=f"AI generation failed: {e}")
