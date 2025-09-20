from __future__ import annotations

import os
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

load_dotenv()

app = FastAPI(title="Gemini Study Buddy API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"]
)

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL = "gemini-2.0-flash"
HTTP_TIMEOUT_SECONDS = 30.0


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

    url = GEMINI_API_BASE.format(model=request.model)
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": request.prompt}],
            }
        ]
    }

    # Use a short-lived HTTP client so we don't hold the event loop open between requests.
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        try:
            response = await client.post(url, params={"key": api_key}, json=payload)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Failed to reach Gemini: {exc}") from exc

    data: dict[str, Any] = {}
    try:
        data = response.json()
    except ValueError:
        raise HTTPException(status_code=502, detail="Gemini returned a non-JSON response.")

    if response.status_code >= 400:
        message = data.get("error", {}).get("message") or f"Gemini API error ({response.status_code})"
        raise HTTPException(status_code=502, detail=message)

    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    output = "".join(part.get("text", "") for part in parts).strip()

    if not output:
        raise HTTPException(status_code=502, detail="Gemini returned an empty response.")

    return GenerateResponse(output=output)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
