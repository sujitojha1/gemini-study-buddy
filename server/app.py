from __future__ import annotations

import json
import os
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from google import genai
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

load_dotenv()

app = FastAPI(title="Gemini Study Buddy API", version="0.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"]
)

DEFAULT_MODEL = "gemini-2.0-flash"
FLASHCARD_COUNT = 5
LOG_PATH = Path(__file__).resolve().parent / "logs" / "agent_history.log"

SUMMARY_PROMPT_TEMPLATE = (
    "Extract the essential study notes from the following learner request. "
    "Focus on crisp facts, definitions, formulas, and conceptual explanations.\n\n"
    "Learner request:\n{user_prompt}"
)

FLASHCARD_PROMPT_TEMPLATE = (
    "You are helping a learner revise.\n"
    "Create up to {flashcard_count} high-quality flashcards using the study summary below.\n"
    "Respond ONLY with a JSON array. Each item must contain:\n"
    "  - \"front\": a short active-recall question or prompt (<= 120 chars).\n"
    "  - \"back\": the concise answer or explanation (<= 240 chars).\n"
    "Do not add commentary before or after the JSON.\n\n"
    "Study summary:\n{study_summary}"
)


class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, description="Raw learner request to turn into study flashcards.")
    model: str = Field(DEFAULT_MODEL, min_length=1, description="Gemini model to use for all calls.")
    api_key: str | None = Field(
        default=None,
        description="Optional Gemini API key. Falls back to GEMINI_API_KEY env var when omitted.",
    )


class Flashcard(BaseModel):
    front: str
    back: str


class GenerateResponse(BaseModel):
    cards: dict[str, Flashcard]
    steps: list[str]
    source_summary: str


class ErrorResponse(BaseModel):
    detail: str


@lru_cache(maxsize=4)
def _get_client(api_key: str) -> genai.Client:
    return genai.Client(api_key=api_key)


def _strip_code_fences(text: str) -> str:
    candidate = text.strip()
    if candidate.startswith("```") and candidate.endswith("```"):
        candidate = candidate[3:-3].strip()
        if candidate.lower().startswith("json"):
            candidate = candidate[4:].strip()
    return candidate


def _extract_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if text:
        return str(text).strip()

    candidates = getattr(response, "candidates", None)
    if candidates is None and isinstance(response, dict):
        candidates = response.get("candidates")

    collected: list[str] = []
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
                    collected.append(str(part_text))

    return "".join(collected).strip()


def _parse_flashcards(raw_text: str, max_cards: int) -> list[Flashcard]:
    cleaned = _strip_code_fences(raw_text)
    parsed: Any
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        raise ValueError("Flashcard response was not valid JSON.")

    if isinstance(parsed, dict):
        parsed_list = [parsed]
    elif isinstance(parsed, list):
        parsed_list = parsed
    else:
        raise ValueError("Flashcard JSON must be an object or an array of objects.")

    flashcards: list[Flashcard] = []
    for idx, item in enumerate(parsed_list, start=1):
        if not isinstance(item, dict):
            continue
        front = str(item.get("front") or item.get("question") or "").strip()
        back = str(item.get("back") or item.get("answer") or "").strip()
        if not front:
            continue
        if not back:
            back = front
        flashcards.append(Flashcard(front=front, back=back))
        if len(flashcards) >= max_cards:
            break

    return flashcards


def _append_history_log(entry: dict[str, Any]) -> None:
    timestamp = entry.get("timestamp") or datetime.utcnow().isoformat() + "Z"
    status = entry.get("status", "unknown")
    model = entry.get("model", "")
    prompt = (entry.get("prompt") or "").strip()
    summary = (entry.get("study_summary") or "").strip()
    summary_prompt = (entry.get("summary_prompt") or "").strip()
    summary_raw = (entry.get("summary_raw") or "").strip()
    cards_prompt = (entry.get("cards_prompt") or "").strip()
    cards_raw = (entry.get("cards_raw") or "").strip()
    steps = entry.get("steps") or []
    cards = entry.get("cards") or {}

    lines: list[str] = []
    lines.append(f"=== Gemini Flashcard Run @ {timestamp} ===")
    lines.append(f"Status: {status}")
    if entry.get("error"):
        lines.append(f"Error: {entry['error']}")
    if model:
        lines.append(f"Model: {model}")
    lines.append(f"Requested Flashcards: {FLASHCARD_COUNT}")
    lines.append("")

    if prompt:
        lines.append("Prompt:")
        lines.append(prompt)
        lines.append("")

    if summary_prompt:
        lines.append("Summary Prompt:")
        lines.append(summary_prompt)
        lines.append("")

    if summary_raw:
        lines.append("Summary Response:")
        lines.append(summary_raw)
        lines.append("")

    if cards_prompt:
        lines.append("Flashcard Prompt:")
        lines.append(cards_prompt)
        lines.append("")

    if cards_raw:
        lines.append("Flashcard Response:")
        lines.append(cards_raw)
        lines.append("")

    if summary:
        lines.append("Study Summary Used:")
        lines.append(summary)
        lines.append("")

    if steps:
        lines.append("Steps:")
        for step in steps:
            lines.append(step)
        lines.append("")

    lines.append("Cards:")
    if cards:
        for idx, (card_id, card) in enumerate(cards.items(), start=1):
            lines.append(f"- {card_id} (#{idx})")
            lines.append(f"  Front: {card.get('front', '').strip()}")
            lines.append(f"  Back: {card.get('back', '').strip()}")
    else:
        lines.append("(none)")
    lines.append("")

    lines.append("=== End Run ===")
    lines.append("")

    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines))
    except Exception:
        pass


@app.get("/health", response_model=dict[str, str])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/generate",
    response_model=GenerateResponse,
    responses={400: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
)
async def generate(request: GenerateRequest) -> GenerateResponse:
    api_key =  os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="Gemini API key missing. Provide it in the request or set GEMINI_API_KEY.",
        )

    model_name = request.model.strip()
    if not model_name:
        raise HTTPException(status_code=400, detail="Model name may not be empty.")
    if not model_name.startswith("models/"):
        model_name = f"models/{model_name}"

    try:
        client = _get_client(api_key)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to initialize Gemini client: {exc}") from exc

    steps: list[str] = []
    cards: dict[str, Flashcard] = {}
    summary_text = ""
    summary_prompt = SUMMARY_PROMPT_TEMPLATE.format(user_prompt=request.prompt.strip())
    cards_prompt = ""
    flashcard_raw = ""

    log_context: dict[str, Any] = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "prompt": request.prompt,
        "model": model_name,
        "flashcard_count": FLASHCARD_COUNT,
        "summary_prompt": summary_prompt,
    }

    try:
        summary_response = await run_in_threadpool(
            client.models.generate_content,
            model=model_name,
            contents=summary_prompt,
        )
        summary_raw = _extract_text(summary_response)
        log_context["summary_raw"] = summary_raw

        if not summary_raw:
            raise HTTPException(status_code=502, detail="Gemini returned an empty study summary.")

        summary_text = summary_raw.strip()
        steps.append("Step 1: Summarised the learner prompt into study notes.")
        log_context["study_summary"] = summary_text

        cards_prompt = FLASHCARD_PROMPT_TEMPLATE.format(
            flashcard_count=FLASHCARD_COUNT,
            study_summary=summary_text,
        )
        log_context["cards_prompt"] = cards_prompt

        flashcard_response = await run_in_threadpool(
            client.models.generate_content,
            model=model_name,
            contents=cards_prompt,
        )
        flashcard_raw = _extract_text(flashcard_response)
        log_context["cards_raw"] = flashcard_raw

        if not flashcard_raw:
            raise HTTPException(status_code=502, detail="Gemini returned empty flashcard content.")

        steps.append("Step 2: Requested up to 5 flashcards from Gemini.")

        try:
            flashcards = _parse_flashcards(flashcard_raw, FLASHCARD_COUNT)
        except ValueError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        steps.append(f"Step 3: Parsed {len(flashcards)} flashcards from Gemini's response.")

        cards = {
            f"card_{index}": card
            for index, card in enumerate(flashcards, start=1)
        }

        response_payload = GenerateResponse(cards=cards, steps=steps, source_summary=summary_text)
        log_context["status"] = "success"
        log_context["steps"] = steps
        log_context["cards"] = {card_id: card.dict() for card_id, card in cards.items()}
        _append_history_log(log_context)
        return response_payload

    except HTTPException as exc:
        log_context["status"] = "error"
        log_context["error"] = str(exc.detail)
        log_context["steps"] = steps
        log_context["cards"] = {card_id: card.dict() for card_id, card in cards.items()}
        if summary_text:
            log_context["study_summary"] = summary_text
        if cards_prompt:
            log_context["cards_prompt"] = cards_prompt
        if flashcard_raw:
            log_context["cards_raw"] = flashcard_raw
        _append_history_log(log_context)
        raise
    except Exception as exc:
        log_context["status"] = "error"
        log_context["error"] = str(exc)
        log_context["steps"] = steps
        log_context["cards"] = {card_id: card.dict() for card_id, card in cards.items()}
        if summary_text:
            log_context["study_summary"] = summary_text
        if cards_prompt:
            log_context["cards_prompt"] = cards_prompt
        if flashcard_raw:
            log_context["cards_raw"] = flashcard_raw
        _append_history_log(log_context)
        raise HTTPException(status_code=502, detail=f"Gemini request failed: {exc}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
