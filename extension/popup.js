const MODEL = "gemini-2.0-flash";
const API_BASE = `https://generativelanguage.googleapis.com/v1beta/models/${MODEL}:generateContent`;

const MAX_CONTEXT_LENGTH = 6000;

const statusEl = document.getElementById("status");
const apiKeyInput = document.getElementById("apiKeyInput");
const apiKeyButton = document.getElementById("apiKeyButton");
const contextSection = document.getElementById("contextSection");
const contextPreview = document.getElementById("contextPreview");
const resultSection = document.getElementById("resultSection");
const resultEl = document.getElementById("result");
const loadingTemplate = document.getElementById("loadingTemplate");

const actionButtons = Array.from(document.querySelectorAll("button[data-action]"));
const KEY_PLACEHOLDER_DEFAULT = apiKeyInput?.getAttribute("placeholder") ?? "Paste your Gemini API key";
const KEY_PLACEHOLDER_LOADED = "API key loaded. Paste a new key to replace it.";
let cachedApiKey = "";

init();

async function init() {
  actionButtons.forEach((button) => {
    button.addEventListener("click", () => handleGenerate(button.dataset.action));
  });

  if (apiKeyButton && apiKeyInput) {
    apiKeyButton.addEventListener("click", handleApiKeySubmit);
    apiKeyInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        handleApiKeySubmit();
      }
    });
    apiKeyInput.addEventListener("input", handleApiKeyTyping);
    markKeyLoaded(false);
  }

  setStatus("Paste your Gemini API key and click Load key to get started.");
}

async function resolveApiKey() {
  return cachedApiKey.trim();
}

async function handleGenerate(action) {
  const apiKey = await resolveApiKey();
  if (!apiKey) {
    setStatus("Paste your Gemini API key and click Load key to get started.", { error: true });
    return;
  }

  try {
    setButtonsDisabled(true);
    setStatus("Collecting the page context...", { loading: true });
    const contextInfo = await collectPageContext();

    if (!contextInfo.text) {
      throw new Error("Couldn't read any text on this page. Try selecting the content first.");
    }

    showContext(contextInfo);

    setStatus("Talking with Gemini Flash...", { loading: true });
    const prompt = buildPrompt(action, contextInfo);
    const output = await callGemini(apiKey, prompt);
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

function handleApiKeySubmit() {
  if (!apiKeyInput) {
    return;
  }

  const value = apiKeyInput.value.trim();
  if (!value) {
    setStatus("Paste your Gemini API key first.", { error: true });
    apiKeyInput.focus();
    return;
  }

  setApiKey(value);
  apiKeyInput.value = "";
  markKeyLoaded(true);
  setStatus("Gemini key loaded! Highlight text for a tighter focus.");
}

function handleApiKeyTyping() {
  if (!apiKeyInput) {
    return;
  }

  const hasValue = apiKeyInput.value.length > 0;

  if (hasValue) {
    markKeyLoaded(false);
  } else {
    markKeyLoaded(Boolean(cachedApiKey));
  }
}

function setApiKey(value) {
  cachedApiKey = value;
}

function markKeyLoaded(loaded) {
  if (!apiKeyInput) {
    return;
  }

  if (loaded) {
    apiKeyInput.setAttribute("data-loaded", "true");
    apiKeyInput.placeholder = KEY_PLACEHOLDER_LOADED;
  } else {
    apiKeyInput.removeAttribute("data-loaded");
    apiKeyInput.placeholder = KEY_PLACEHOLDER_DEFAULT;
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

function showContext({ text, truncated, usedSelection }) {
  if (!text) {
    contextSection.hidden = true;
    return;
  }

  contextPreview.textContent = text;
  contextSection.hidden = false;

  const heading = contextSection.querySelector("h2");
  if (heading) {
    if (usedSelection) {
      heading.textContent = "Context from your highlighted selection";
    } else {
      heading.textContent = "Context extracted from the page";
    }
    if (truncated) {
      heading.textContent += " (truncated)";
    }
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
    case "summary":
      task = `Create a study summary that captures the essential ideas, definitions, and cause/effect relationships.
- Start with a short paragraph that frames the topic.
- Follow with 3-6 bullet points of key takeaways using plain language.
- Close with one actionable tip or mnemonic that helps remember the material.`;
      break;
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
      task = "Summarize the material in a learner-friendly way.";
  }

  return `You are Gemini Flashcards Tutor, an expert AI study assistant powered by Google Gemini Flash 2.0.
Use the context provided to craft the requested study aid.${selectionNote}${truncationNote}

Context:
"""
${text}
"""

Task:
${task}`;
}

async function callGemini(apiKey, prompt) {
  const response = await fetch(`${API_BASE}?key=${encodeURIComponent(apiKey)}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      contents: [
        {
          role: "user",
          parts: [{ text: prompt }],
        },
      ],
    }),
  });

  const payload = await response.json().catch(() => ({}));

  if (!response.ok) {
    const errorMessage = payload?.error?.message || `Gemini API error (${response.status})`;
    throw new Error(errorMessage);
  }

  const text = payload?.candidates?.[0]?.content?.parts
    ?.map((part) => part?.text ?? "")
    .join("")
    .trim();

  if (!text) {
    throw new Error("Gemini returned an empty response. Try again or adjust the highlighted content.");
  }

  return text;
}
