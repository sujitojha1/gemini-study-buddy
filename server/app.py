from __future__ import annotations

import ast
import math
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
DEFAULT_MAX_ITERATIONS = 3
AGENT_SYSTEM_PROMPT = (
    "You are a helpful study assistant that can call Python tools to solve tasks. "
    "ALWAYS respond with EXACTLY ONE of the following formats on each turn:\n"
    "1. FUNCTION_CALL: python_function_name|input\n"
    "2. FINAL_ANSWER: answer\n\n"
    "Available python_function_name values:\n"
    "- strings_to_chars_to_int(string): returns ASCII integer values for each character in the string\n"
    "- int_list_to_exponential_sum(list[int]): returns the sum of exponentials for the provided integers\n"
    "- fibonacci_numbers(int): returns the first n Fibonacci numbers as a list\n"
    "- format_flash_card(text): converts narrative content into a 'Front:'/'Back:' flash card string\n\n"
    "Always finish by calling format_flash_card before issuing your FINAL_ANSWER so the user receives a flash card."
)


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


def strings_to_chars_to_int(string: str) -> list[int]:
    return [ord(char) for char in string]


def int_list_to_exponential_sum(values: list[int]) -> float:
    return sum(math.exp(i) for i in values)


def fibonacci_numbers(n: int) -> list[int]:
    if n <= 0:
        return []
    sequence = [0, 1]
    for _ in range(2, n):
        sequence.append(sequence[-1] + sequence[-2])
    return sequence[:n]


def format_flash_card(content: str) -> str:
    """Format content into a simple Front/Back flash card."""
    text = content.strip()
    if not text:
        return "Front: (empty)\nBack: (empty)"

    # Heuristically split on the first blank line or newline.
    if "\n\n" in text:
        front_part, back_part = text.split("\n\n", 1)
    elif "\n" in text:
        front_part, back_part = text.split("\n", 1)
    elif ":" in text:
        front_part, back_part = text.split(":", 1)
        front_part = front_part.strip()
        back_part = back_part.strip()
    else:
        front_part, back_part = text, ""

    front = front_part.strip() or "(empty)"
    back = back_part.strip() or front

    return f"Front: {front}\nBack: {back}"


def _call_tool(func_name: str, raw_params: str) -> tuple[str, Any]:
    tools: dict[str, Any] = {
        "strings_to_chars_to_int": lambda param: strings_to_chars_to_int(param),
        "int_list_to_exponential_sum": lambda param: int_list_to_exponential_sum(param),
        "fibonacci_numbers": lambda param: fibonacci_numbers(param),
        "format_flash_card": lambda param: format_flash_card(param),
    }

    if func_name not in tools:
        raise ValueError(f"Function {func_name} not found")

    if func_name in {"strings_to_chars_to_int", "format_flash_card"}:
        parsed_params = raw_params.strip()
        if len(parsed_params) >= 2 and parsed_params[0] == parsed_params[-1] and parsed_params[0] in {'"', "'"}:
            parsed_params = parsed_params[1:-1]
    elif func_name == "int_list_to_exponential_sum":
        try:
            parsed_params = ast.literal_eval(raw_params)
        except (ValueError, SyntaxError) as exc:
            raise ValueError(f"Unable to parse list arguments: {raw_params}") from exc
        if not isinstance(parsed_params, list) or not all(isinstance(item, int) for item in parsed_params):
            raise ValueError("int_list_to_exponential_sum expects a list of integers")
    elif func_name == "fibonacci_numbers":
        try:
            parsed_params = int(raw_params)
        except ValueError as exc:
            raise ValueError(f"Unable to parse integer argument: {raw_params}") from exc
    else:
        parsed_params = raw_params

    result = tools[func_name](parsed_params)
    return raw_params, result


def _build_agent_prompt(query: str, history: list[str]) -> str:
    if not history:
        return f"{AGENT_SYSTEM_PROMPT}\n\nQuery: {query.strip()}"

    history_block = "\n\n".join(history)
    return f"{AGENT_SYSTEM_PROMPT}\n\nQuery: {query.strip()}\n\n{history_block}\n\nWhat should I do next?"


def _parse_function_call(response_text: str) -> tuple[str, str]:
    try:
        _, payload = response_text.split(":", 1)
        func_name, raw_params = [item.strip() for item in payload.split("|", 1)]
    except ValueError as exc:
        raise ValueError(f"Malformed FUNCTION_CALL response: {response_text}") from exc

    return func_name, raw_params


class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, description="Fully composed prompt to forward to Gemini")
    model: str = Field(DEFAULT_MODEL, min_length=1, description="Gemini model name")
    api_key: str | None = Field(
        default=None,
        description="Optional Gemini API key. Falls back to GEMINI_API_KEY env var when omitted.",
    )
    max_iterations: int = Field(
        DEFAULT_MAX_ITERATIONS,
        ge=1,
        le=10,
        description="How many LLM/tool iterations to allow before giving up.",
    )


class GenerateResponse(BaseModel):
    output: str = Field(..., description="Combined text output from Gemini")
    steps: list[str] = Field(default_factory=list, description="Trace of LLM responses and tool invocations.")


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
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to initialize Gemini client: {exc}") from exc

    history: list[str] = []
    steps: list[str] = []
    final_answer: str | None = None

    for iteration in range(request.max_iterations):
        prompt = _build_agent_prompt(request.prompt, history)

        try:
            response = await run_in_threadpool(
                client.models.generate_content,
                model=model_name,
                contents=prompt,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Failed to reach Gemini: {exc}") from exc

        response_text = _extract_text(response)
        if not response_text:
            raise HTTPException(status_code=502, detail="Gemini returned an empty response.")

        steps.append(f"Iteration {iteration + 1} LLM: {response_text}")

        normalized = response_text.strip()
        if normalized.startswith("FINAL_ANSWER:"):
            final_answer = normalized.split(":", 1)[1].strip()
            break

        if normalized.startswith("FUNCTION_CALL:"):
            try:
                func_name, raw_params = _parse_function_call(normalized)
                original_params, tool_result = _call_tool(func_name, raw_params)
            except ValueError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc

            steps.append(
                f"Iteration {iteration + 1} Tool: {func_name}({original_params}) -> {tool_result}"
            )
            history.append(
                f"In iteration {iteration + 1} you called {func_name} with {original_params} parameters, and the function returned {tool_result}."
            )
            continue

        raise HTTPException(
            status_code=502,
            detail="Gemini returned an unexpected response format; expected FUNCTION_CALL or FINAL_ANSWER.",
        )

    if final_answer is None:
        raise HTTPException(status_code=502, detail="Gemini agent did not return a final answer within iteration limit.")

    return GenerateResponse(output=final_answer, steps=steps)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
