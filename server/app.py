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
LOG_PATH = Path(__file__).resolve().parent / "logs" / f"agent_history_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
LOG_LINE_WIDTH = 72

SUMMARY_PROMPT_TEMPLATE = (
    "Extract the essential study notes from the learner material below. "
    "Focus on crisp facts, definitions, formulas, and conceptual explanations.\n"
    "{selection_note}{truncation_note}\n"
    "Learner material:\n{page_text}"
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


class PageContext(BaseModel):
    text: str = Field(..., min_length=1, description="Combined selection or page text extracted from the learner's web page.")
    truncated: bool = Field(False, description="True if the original page content was truncated for length.")
    used_selection: bool = Field(False, description="True if the learner highlighted specific text instead of using the full page body.")


class GenerateRequest(BaseModel):
    page_context: PageContext = Field(..., description="Structured representation of the learner's captured page content.")


class Flashcard(BaseModel):
    front: str
    back: str


class GenerateResponse(BaseModel):
    cards: dict[str, Flashcard]
    steps: list[str]
    source_summary: str


class ErrorResponse(BaseModel):
    detail: str


def _log_lines(lines: list[str], *, header: bool = False) -> None:
    """Append formatted lines to the request log."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as log_file:
        if header:
            separator = "=" * LOG_LINE_WIDTH
            log_file.write(f"\n{separator}\n")
        for line in lines:
            log_file.write(f"{line}\n")
        if header:
            log_file.write("=" * LOG_LINE_WIDTH + "\n")


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




@app.get("/health", response_model=dict[str, str])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/generate",
    response_model=GenerateResponse,
    responses={400: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
)
async def generate(request: GenerateRequest) -> GenerateResponse:
    # Capture a trimmed version of the learner content to keep logs readable.
    page_context = request.page_context
    request_text = page_context.text.strip()
    preview = " ".join(request_text.split())[:160]
    if len(request_text) > 160:
        preview = f"{preview}..."

    # Log a header so each request is easy to spot in the rolling agent history file.
    _log_lines(
        [
            f"{'Gemini Study Buddy Request':^{LOG_LINE_WIDTH}}",
            f"Started: {datetime.now().isoformat()}",
            f"Preview: {preview}" if preview else "Preview: [empty]",
        ],
        header=True,
    )

    api_key = os.getenv("GEMINI_API_KEY")
    print("stage1")

    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="Gemini API key missing. Provide it in the request or set GEMINI_API_KEY.",
        )

    model_name = DEFAULT_MODEL

    try:
        client = _get_client(api_key)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to initialize Gemini client: {exc}") from exc

    # `steps` is returned to the client so the UI can display high-level progress.
    steps: list[str] = []
    cards: dict[str, Flashcard] = {}
    summary_text = ""
    selection_note = ""
    if page_context.used_selection:
        selection_note = (
            "The learner highlighted specific text. Prioritize the highlighted material while still using the broader context when relevant.\n"
        )

    truncation_note = ""
    if page_context.truncated:
        truncation_note = (
            "The context was truncated for length. Respond using only the provided portion.\n"
        )

    summary_prompt = SUMMARY_PROMPT_TEMPLATE.format(
        page_text=request_text,
        selection_note=selection_note,
        truncation_note=truncation_note,
    )
    cards_prompt = ""
    flashcard_raw = ""


    try:
        # Offload Gemini calls to a worker thread to keep the event loop free for I/O.
        summary_response = await run_in_threadpool(
            client.models.generate_content,
            model=model_name,
            contents=summary_prompt,
        )
        summary_raw = _extract_text(summary_response)
        

        if not summary_raw:
            raise HTTPException(status_code=502, detail="Gemini returned an empty study summary.")

        summary_text = summary_raw.strip()
        summary_status = "Status: Study summary received from Gemini."
        steps.append("Study summary received from Gemini.")
        _log_lines([summary_status])


        cards_prompt = FLASHCARD_PROMPT_TEMPLATE.format(
            flashcard_count=FLASHCARD_COUNT,
            study_summary=summary_text,
        )


        flashcard_response = await run_in_threadpool(
            client.models.generate_content,
            model=model_name,
            contents=cards_prompt,
        )
        flashcard_raw = _extract_text(flashcard_response)
        

        if not flashcard_raw:
            raise HTTPException(status_code=502, detail="Gemini returned empty flashcard content.")


        try:
            flashcards = _parse_flashcards(flashcard_raw, FLASHCARD_COUNT)
        except ValueError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        cards = {
            f"card_{index}": card
            for index, card in enumerate(flashcards, start=1)
        }

        cards_status = "Status: Flashcards generated successfully."
        steps.append("Flashcards generated successfully.")
        _log_lines([cards_status])

        response_payload = GenerateResponse(cards=cards, steps=steps, source_summary=summary_text)
        return response_payload

    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        # Surface the failure reason to the log before handing control back to FastAPI.
        _log_lines([f"Status: Request failed ({exc.status_code}): {detail}"])
        raise
    except Exception as exc:
        # Unknown exceptions get logged and wrapped so the client receives a consistent error.
        _log_lines([f"Status: Unexpected error: {exc}"])
        raise HTTPException(status_code=502, detail=f"Gemini request failed: {exc}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
