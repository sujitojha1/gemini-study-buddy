from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from google import genai
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

load_dotenv()

app = FastAPI(title="Gemini Study Buddy API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"]
)

DEFAULT_MODEL = "gemini-2.0-flash"


@lru_cache(maxsize=4)
def _get_client(api_key: str) -> genai.Client:
    """Cache clients per API key to avoid re-instantiation overhead."""
    return genai.Client(api_key=api_key)


def _extract_text(response: Any) -> str:
    """Pull the combined text from a Google GenAI response."""
    text = getattr(response, "text", None)
    if text:
        return str(text).strip()

    # Fall back to iterating candidate parts. Handles both dicts and typed objects.
    candidates = getattr(response, "candidates", None)
    if candidates is None and isinstance(response, dict):
        candidates = response.get("candidates")

    texts: list[str] = []
    if candidates:
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            if content is None and isinstance(candidate, dict):
                content = candidate.get("content")

            parts = getattr(content, "parts", None) if content is not None else None
            if parts is None and isinstance(content, dict):
                parts = content.get("parts")

            if not parts:
                continue

            for part in parts:
                part_text = getattr(part, "text", None)
                if part_text is None and isinstance(part, dict):
                    part_text = part.get("text")

                if part_text:
                    texts.append(str(part_text))

    return "".join(texts).strip()


class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, description="Fully composed prompt to forward to Gemini")
    model: str = Field(DEFAULT_MODEL, min_length=1, description="Gemini model name")
    api_key: str | None = Field(
        default=None,
        description="Optional Gemini API key. Falls back to GEMINI_API_KEY env var when omitted.",
    )


class GenerateResponse(BaseModel):
    output: str = Field(..., description="Combined text output from Gemini")


class ErrorResponse(BaseModel):
    detail: str


@app.get("/health", response_model=dict[str, str])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/generate", response_model=GenerateResponse, responses={400: {"model": ErrorResponse}, 502: {"model": ErrorResponse}})
async def generate(request: GenerateRequest) -> GenerateResponse:
    api_key = (request.api_key or os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="Gemini API key missing. Provide it in the request or set GEMINI_API_KEY.")

    model_name = request.model.strip()
    if not model_name:
        raise HTTPException(status_code=400, detail="Model name may not be empty.")

    if not model_name.startswith("models/"):
        model_name = f"models/{model_name}"

    try:
        client = _get_client(api_key)
        response = await run_in_threadpool(
            client.models.generate_content,
            model=model_name,
            contents=request.prompt,
        )
    except Exception as exc:  # google-genai raises rich subclasses, but HTTPException needs str.
        raise HTTPException(status_code=502, detail=f"Failed to reach Gemini: {exc}") from exc

    output = _extract_text(response)

    if not output:
        raise HTTPException(status_code=502, detail="Gemini returned an empty response.")

    return GenerateResponse(output=output)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
