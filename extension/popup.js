const MODEL = "gemini-2.0-flash";
const LOCAL_API_BASE = "http://127.0.0.1:8000";
const LOCAL_GENERATE_ENDPOINT = `${LOCAL_API_BASE}/generate`;

const MAX_CONTEXT_LENGTH = 6000;

const statusEl = document.getElementById("status");
const resultSection = document.getElementById("resultSection");
const resultEl = document.getElementById("result");
const loadingTemplate = document.getElementById("loadingTemplate");

const actionButtons = Array.from(document.querySelectorAll("button[data-action]"));

init();

async function init() {
  actionButtons.forEach((button) => {
    button.addEventListener("click", () => handleGenerate(button.dataset.action));
  });

  setStatus("Highlight what matters or just run it on the full page.");
}

async function handleGenerate(action) {
  try {
    setButtonsDisabled(true);
    setStatus("Collecting the page context...", { loading: true });
    const contextInfo = await collectPageContext();

    if (!contextInfo.text) {
      throw new Error("Couldn't read any text on this page. Try selecting the content first.");
    }

    setStatus("Talking with Gemini...", { loading: true });
    const prompt = buildPrompt(action, contextInfo);
    const output = await callGemini(prompt);
    showResult(output);
    setStatus("Done! Review the study material below.");
  } catch (error) {
    console.error(error);
    const message = error?.message || "Something went wrong while generating.";
    setStatus(message, { error: true });
  } finally {
    setButtonsDisabled(false);
  }
}

function setButtonsDisabled(disabled) {
  actionButtons.forEach((button) => {
    button.disabled = disabled;
  });
}

function setStatus(message, { loading = false, error = false } = {}) {
  statusEl.innerHTML = "";
  statusEl.classList.toggle("error", Boolean(error));
  statusEl.hidden = !message && !loading;

  if (loading) {
    const fragment = loadingTemplate.content.cloneNode(true);
    const span = fragment.querySelector("span");
    if (span) {
      span.textContent = message || "Working...";
    }
    statusEl.appendChild(fragment);
  } else if (message) {
    statusEl.textContent = message;
  }
}

async function collectPageContext() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) {
    throw new Error("Couldn't find the active tab.");
  }

  try {
    const [injectionResult] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => {
        const selection = window.getSelection()?.toString() ?? "";
        const bodyText = document.body?.innerText ?? "";
        return {
          selection: selection.trim(),
          body: bodyText.trim().replace(/\s+\n/g, "\n").replace(/\n{3,}/g, "\n\n"),
        };
      },
    });

    const { selection, body } = injectionResult?.result ?? { selection: "", body: "" };
    let text = selection || body || "";
    const usedSelection = Boolean(selection);
    let truncated = false;

    if (text.length > MAX_CONTEXT_LENGTH) {
      truncated = true;
      text = `${text.slice(0, MAX_CONTEXT_LENGTH)}\n\n[Context truncated after ${MAX_CONTEXT_LENGTH} characters]`;
    }

    return { text, truncated, usedSelection };
  } catch (error) {
    console.error("Failed to inject script", error);
    throw new Error("Chrome couldn't access this tab. Try reloading the page and opening the extension again.");
  }
}

function showResult(output) {
  resultEl.textContent = output;
  resultSection.hidden = false;
}

function buildPrompt(action, contextInfo) {
  const { text, truncated, usedSelection } = contextInfo;
  const selectionNote = usedSelection
    ? "\nThe learner highlighted specific text. Prioritize the highlighted material while still using broader context when relevant."
    : "";
  const truncationNote = truncated
    ? "\nThe context was truncated for length. Respond using only the provided portion."
    : "";

  let task;
  switch (action) {
    case "flashcards":
      task = `Produce 6-10 concise flashcards formatted as Markdown.
For each card write a bold question line followed by an indented answer line that learners can quickly review.
Vary between concept, definition, and application questions.`;
      break;
    case "quiz":
      task = `Generate a short self-check quiz in Markdown with 5 multiple-choice questions.
For each question provide four answer options labeled A-D and mark the correct answer on a new line starting with "Answer:".
Include a one-sentence explanation after each answer to reinforce learning.`;
      break;
    default:
      throw new Error("Unsupported action requested.");
  }

  return `You are Gemini Study Buddy, an expert AI study assistant powered by Google Gemini Flash 2.0.
Use the context provided to craft the requested study aid.${selectionNote}${truncationNote}

Context:
"""
${text}
"""

Task:
${task}`;
}

async function callGemini(prompt) {
  let response;
  try {
    response = await fetch(LOCAL_GENERATE_ENDPOINT, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        prompt,
        model: MODEL,
      }),
    });
  } catch (error) {
    console.error("Local API network failure", error);
    throw new Error("Could not reach the local Gemini server. Start the FastAPI app and try again.");
  }

  const payload = await response.json().catch(() => ({}));

  if (!response.ok) {
    const errorMessage = payload?.detail || `Local API error (${response.status})`;
    throw new Error(errorMessage);
  }

  const text = payload?.output?.trim?.();

  if (!text) {
    throw new Error("Local API returned an empty response. Check the FastAPI logs and try again.");
  }

  return text;
}
