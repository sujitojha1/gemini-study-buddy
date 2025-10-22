from __future__ import annotations

import json
import re
import os
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

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

api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    raise HTTPException(
        status_code=400,
        detail="Gemini API key missing. Provide it in the request or set GEMINI_API_KEY.",
    )

SUMMARY_PROMPT_TEMPLATE = (
    "Extract the essential study notes from the learner material below. "
    "Focus on crisp facts, definitions, formulas, and conceptual explanations.\n"
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
    content_rating: int | None = None
    information_hierarchy: str | None = None


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


def rate_content_quality(
    content: str
) -> int:
    """
    Returns an integer 1–10:
      1–4  => mostly ephemeral mention/news with low depth
      5–7  => some explanatory value
      8–10 => strong knowledge-building (concepts, mechanisms, transferable insight)
    """
    prompt = f"""
    You are a strict rater of learning value.

    Task: Rate the following text on a 1–10 scale for how *useful it is to gain durable knowledge* (vs. being mere headlines/news).

    Guidelines:
    - 1–4: brief mentions, announcements, time-bound news
    - 5–7: some explanation, limited depth or transfer
    - 8–10: clear concepts, mechanisms, examples, definitions, procedures

    Return ONLY the integer.

    Text:
    \"\"\"{content}\"
    \"\"\"
    """
    try:
        client = _get_client(api_key)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to initialize Gemini client: {exc}") from exc
    
    out = client.models.generate_content(model="gemini-2.0-flash",contents=prompt)
    m = re.search(r"\b(10|[1-9])\b", out.text)
    return int(m.group(1)) if m else 5

def generate_flashcards_json(
    study_summary: str
) -> List[Dict[str, str]]:
    """
    Produces up to `max_cards` active-recall flashcards as a STRICT JSON array:
    [
      {"front": "...", "back": "..."},
      ...
    ]
    - front: <=120 chars (question/prompt)
    - back:  <=240 chars (concise answer)
    If the model returns extra text, we attempt to extract and validate the array.
    """

    max_cards = 5
    prompt = f"""
You are helping a learner revise.

Create up to {max_cards} high-quality flashcards using the study summary below.

Respond ONLY with a JSON array. Each item must contain:
  - "front": a short active-recall question or prompt (<= 120 chars).
  - "back": the concise answer or explanation (<= 240 chars).

Do not add commentary before or after the JSON.

Study summary:
\"\"\"{study_summary}\"
\"\"\"
"""
    try:
        client = _get_client(api_key)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to initialize Gemini client: {exc}") from exc
    
    out = client.models.generate_content(model="gemini-2.0-flash",contents=prompt)
    return out.text

def infer_information_hierarchy_and_jobs_simple(
    content: str
) -> str:
    """
    Returns a plain text tree-like concept hierarchy plus
    a final line: "Note about job function: <>, <>"
    """
    prompt = f"""
Analyze the following text.

1. Build a simple tree-like concept hierarchy (<= 3 levels). 
   Format with indentation using dashes or arrows, like:

Topic
- Subtopic
  - Detail

2. At the end, add one line:
"Note about job function: <job1>, <job2>"

Keep it brief and textual. Do not return JSON.

Text:
\"\"\"{content}\"\"\"
"""
    try:
        client = _get_client(api_key)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to initialize Gemini client: {exc}") from exc
    
    out = client.models.generate_content(model="gemini-2.0-flash",contents=prompt)
    return out.text.strip()

def function_caller(func_name, params):
    """Simple function caller that maps function names to actual functions"""
    function_map = {
        "rate_content_quality": rate_content_quality,
        "infer_information_hierarchy_and_jobs_simple": infer_information_hierarchy_and_jobs_simple,
        "generate_flashcards_json": generate_flashcards_json
    }
    
    if func_name not in function_map:
        raise ValueError(f"Function {func_name} not found")

    return function_map[func_name](params)

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

    # Log a header so each request is easy to spot in the rolling agent history file.
    _log_lines(
        [
            f"{'Gemini Study Buddy Request':^{LOG_LINE_WIDTH}}",
            f"Started: {datetime.now().isoformat()}",
        ],
        header=True,
    )



    model_name = DEFAULT_MODEL

    try:
        client = _get_client(api_key)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to initialize Gemini client: {exc}") from exc

    # `steps` is returned to the client so the UI can display high-level progress.
    steps: list[str] = []
    cards: dict[str, Flashcard] = {}

    try:
        max_iterations = 4
        iteration = 0
        iteration_response: list[str] = []
        flashcards: list[Flashcard] | None = None
        content_rating: int | None = None
        information_hierarchy: str | None = None

        system_prompt = """You are a learning-assistant agent preparing flashcards in iterations.
Your goal: help the learner deeply grasp content meaningfully.

Respond with EXACTLY ONE of these two formats:
1. FUNCTION_CALL: <python_function_name>|<input_text>
2. FINAL_ANSWER: <json_array>

The python_function_name MUST be one of and follow the sequence:
- rate_content_quality
- infer_information_hierarchy_and_jobs_simple
- generate_flashcards_json

Rules:
- Do NOT add any text, explanations, or markdown.
- Output MUST begin with FUNCTION_CALL: or FINAL_ANSWER: as the very first characters (no spaces, no newlines).
- Never include multiple responses or commentary.

Example valid responses:
FUNCTION_CALL: rate_content_quality|....
FINAL_ANSWER: [{"front":"What is AI?","back":"AI is..."}]

Let's solve this step by step.
"""

        base_query = request_text
        current_query = base_query

        while iteration < max_iterations:
            print(f"\n--- Iteration {iteration + 1} ---")

            prompt = f"{system_prompt}\n\nQuery: web page content - {current_query}"
            response = await run_in_threadpool(
                client.models.generate_content,
                model=model_name,
                contents=prompt,
            )

            response_text = (response.text or "").strip()
            

            if response_text.startswith("FUNCTION_CALL:"):
                try:
                    _, function_info = response_text.split(":", 1)
                    func_name, params = [x.strip() for x in function_info.split("|", 1)]
                    print(f"LLM Response: FUNCTION_CALL: {func_name} ")
                except ValueError as exc:
                    raise HTTPException(status_code=502, detail="Gemini returned an invalid function call.") from exc

                try:
                    iteration_result = await run_in_threadpool(function_caller, func_name, params)
                    print(f"Results :{iteration_result} ")
                except Exception as exc:
                    raise HTTPException(status_code=502, detail=f"Function {func_name} failed: {exc}") from exc

                if func_name == "rate_content_quality":
                    try:
                        content_rating = int(iteration_result)
                    except (TypeError, ValueError):
                        content_rating = None
                elif func_name == "infer_information_hierarchy_and_jobs_simple":
                    information_hierarchy = str(iteration_result).strip()

                iteration_response.append(
                    f"In the {iteration + 1} iteration you called {func_name} with {params} parameters, and the function returned {iteration_result}."
                )
                current_query = "\n\n".join(
                    [base_query, " ".join(iteration_response), "What should I do next?"]
                )
                iteration += 1
                continue

            if response_text.startswith("FINAL_ANSWER:"):
                final_payload = response_text[len("FINAL_ANSWER:") :].strip()
                print(f"LLM Response: FINAL_ANSWER: {final_payload} ")
                if not final_payload:
                    raise HTTPException(status_code=502, detail="Gemini returned empty flashcard content.")

                try:
                    flashcards = _parse_flashcards(final_payload, FLASHCARD_COUNT)
                    print(f"Results: {flashcards} ")
                except ValueError as exc:
                    raise HTTPException(status_code=502, detail=str(exc)) from exc

                print("\n=== Agent Execution Complete ===")
                break

            raise HTTPException(status_code=502, detail="Gemini response missing FUNCTION_CALL or FINAL_ANSWER prefix.")

        if flashcards is None:
            raise HTTPException(status_code=502, detail="Gemini agent did not produce a final answer.")


        cards = {
            f"card_{index}": card
            for index, card in enumerate(flashcards, start=1)
        }

        cards_status = "Status: Flashcards generated successfully."
        steps.append("Flashcards generated successfully.")
        _log_lines([cards_status])

        response_payload = GenerateResponse(
            cards=cards,
            steps=steps,
            content_rating=content_rating,
            information_hierarchy=information_hierarchy,
        )
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
