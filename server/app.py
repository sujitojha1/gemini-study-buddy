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

app = FastAPI(title="Gemini Study Buddy API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"]
)

DEFAULT_MODEL = "gemini-2.0-flash"
DEFAULT_FLASHCARD_COUNT = 5
AGENT_MAX_ITERATIONS = 10
SUMMARY_PROMPT_TEMPLATE = (
    "Extract the essential study notes from the following learner request. "
    "Focus on crisp facts, definitions, formulas, and conceptual explanations.\n\n"
    "Learner request:\n{user_prompt}"
)
LOG_PATH = Path(__file__).resolve().parent / "logs" / "agent_history.log"


AGENT_SYSTEM_PROMPT = (
    "You are a collaborative flashcard author.\n"
    "Goal: create exactly {flashcard_count} high-quality flashcards from the provided study summary.\n"
    "Use the available tool to format each flashcard.\n\n"
    "ALLOWED RESPONSES (return exactly one per turn):\n"
    "1. FUNCTION_CALL: format_flash_card|<json_payload>\n"
    "   - json_payload must be a JSON object with 'front' and 'back' strings.\n"
    "2. FINAL_JSON: <json_payload>\n"
    "   - Send this only after all flashcards are formatted.\n"
    "   - Include any short completion metadata you find useful.\n\n"
    "Workflow guidelines:\n"
    "- Analyze the study summary.\n"
    "- For each flashcard, craft concise 'front' (question/prompt) and 'back' (answer/explanation).\n"
    "- Call format_flash_card with JSON like {{\"front\": \"...\", \"back\": \"...\"}}.\n"
    "- After you have formatted {flashcard_count} flashcards, respond with FINAL_JSON to confirm completion.\n"
    "- Do not share raw flashcard text that has not been formatted via the tool.\n"
)


class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, description="Raw learner request to turn into study flashcards.")
    model: str = Field(DEFAULT_MODEL, min_length=1, description="Gemini model to use for all calls.")
    flashcard_count: int = Field(
        DEFAULT_FLASHCARD_COUNT,
        ge=1,
        le=10,
        description="Number of flashcards to create.",
    )
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


def format_flash_card(payload: dict[str, Any]) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise ValueError("Flashcard payload must be a JSON object.")

    front = str(payload.get("front", "")).strip()
    back = str(payload.get("back", "")).strip()

    if not front:
        raise ValueError("Flashcard front may not be empty.")
    if not back:
        back = front

    return {"front": front, "back": back}


def _parse_function_call(response_text: str) -> tuple[str, str]:
    try:
        prefix, payload = response_text.split(":", 1)
    except ValueError as exc:
        raise ValueError(f"Malformed agent response: {response_text}") from exc

    func_name, raw_params = [part.strip() for part in payload.split("|", 1)]
    return func_name, raw_params


def _append_history_log(entry: dict[str, Any]) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False))
            handle.write("\n")
    except Exception:
        # Avoid surfacing logging issues to the client; best effort only.
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
    api_key = (request.api_key or os.getenv("GEMINI_API_KEY") or "").strip()
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

    # Step 1: Summarise the learner prompt into study notes.
    summary_prompt = SUMMARY_PROMPT_TEMPLATE.format(user_prompt=request.prompt.strip())
    try:
        summary_response = await run_in_threadpool(
            client.models.generate_content,
            model=model_name,
            contents=summary_prompt,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to reach Gemini: {exc}") from exc

    study_summary = _extract_text(summary_response)
    if not study_summary:
        raise HTTPException(status_code=502, detail="Gemini returned an empty study summary.")

    steps: list[str] = ["Step 1: Generated study summary from learner prompt."]

    # Step 2: Multi-step agent to create formatted flashcards.
    flashcard_goal = (
        "Use the study summary below to create high quality flashcards."
        f"\n\nStudy summary:\n{study_summary}\n"
    )
    system_prompt = AGENT_SYSTEM_PROMPT.format(flashcard_count=request.flashcard_count)
    history: list[str] = []
    cards: dict[str, Flashcard] = {}
    card_counter = 0

    log_context: dict[str, Any] = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "prompt": request.prompt,
        "model": model_name,
        "flashcard_count": request.flashcard_count,
        "study_summary": study_summary,
    }

    try:
        for iteration in range(AGENT_MAX_ITERATIONS):
            history_block = "\n\n".join(history)
            if history_block:
                agent_prompt = f"{system_prompt}\n\nQuery: {flashcard_goal}\n\n{history_block}\n\nWhat should I do next?"
            else:
                agent_prompt = f"{system_prompt}\n\nQuery: {flashcard_goal}"

            try:
                agent_response = await run_in_threadpool(
                    client.models.generate_content,
                    model=model_name,
                    contents=agent_prompt,
                )
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"Failed to reach Gemini during agent run: {exc}") from exc

            agent_text = _extract_text(agent_response)
            if not agent_text:
                raise HTTPException(status_code=502, detail="Gemini agent returned an empty response.")

            steps.append(f"Step 2.{iteration + 1}: Agent response -> {agent_text}")
            normalized = agent_text.strip()

            if normalized.startswith("FINAL_JSON:"):
                if len(cards) != request.flashcard_count:
                    raise HTTPException(
                        status_code=502,
                        detail=(
                            "Gemini agent signaled completion without producing the expected number of flashcards."
                        ),
                    )
                break

            if normalized.startswith("FUNCTION_CALL:"):
                try:
                    func_name, raw_params = _parse_function_call(normalized)
                except ValueError as exc:
                    raise HTTPException(status_code=502, detail=str(exc)) from exc

                if func_name != "format_flash_card":
                    raise HTTPException(
                        status_code=502,
                        detail=f"Unsupported tool requested: {func_name}",
                    )

                try:
                    payload = json.loads(raw_params)
                except json.JSONDecodeError as exc:
                    raise HTTPException(status_code=502, detail=f"Invalid flashcard JSON: {exc}") from exc

                try:
                    formatted = format_flash_card(payload)
                except ValueError as exc:
                    raise HTTPException(status_code=502, detail=str(exc)) from exc

                card_counter += 1
                card_id = payload.get("id") or f"card_{card_counter}"
                cards[card_id] = Flashcard(**formatted)

                history.append(
                    f"In iteration {iteration + 1}, you formatted flashcard {card_id}: front='{formatted['front']}' back='{formatted['back']}'."
                )
                steps.append(
                    f"Step 2.{iteration + 1}: Stored flashcard {card_id} with front='{formatted['front']}'."
                )

                if len(cards) == request.flashcard_count:
                    history.append(
                        "All required flashcards are prepared. Respond with FINAL_JSON to confirm completion."
                    )

                continue

            raise HTTPException(
                status_code=502,
                detail="Gemini agent returned an unexpected response format.",
            )
        else:
            raise HTTPException(
                status_code=502,
                detail="Gemini agent did not finish within the allowed iterations.",
            )
    except HTTPException as exc:
        log_context["status"] = "error"
        log_context["error"] = str(exc.detail)
        log_context["steps"] = steps
        log_context["cards"] = {card_id: card.dict() for card_id, card in cards.items()}
        _append_history_log(log_context)
        raise
    except Exception as exc:
        log_context["status"] = "error"
        log_context["error"] = str(exc)
        log_context["steps"] = steps
        log_context["cards"] = {card_id: card.dict() for card_id, card in cards.items()}
        _append_history_log(log_context)
        raise HTTPException(status_code=502, detail=f"Gemini agent failed unexpectedly: {exc}") from exc

    response_payload = GenerateResponse(cards=cards, steps=steps, source_summary=study_summary)

    log_context["status"] = "success"
    log_context["steps"] = steps
    log_context["cards"] = {card_id: card.dict() for card_id, card in cards.items()}
    _append_history_log(log_context)

    return response_payload


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
