const MODEL = "gemini-1.5-flash-latest";
const API_BASE = `https://generativelanguage.googleapis.com/v1beta/models/${MODEL}:generateContent`;
const MAX_CONTEXT_LENGTH = 6000;

const apiKeyInput = document.getElementById("apiKey");
const focusInput = document.getElementById("focus");
const statusEl = document.getElementById("status");
const contextSection = document.getElementById("contextSection");
const contextPreview = document.getElementById("contextPreview");
const resultSection = document.getElementById("resultSection");
const resultEl = document.getElementById("result");
const loadingTemplate = document.getElementById("loadingTemplate");

const actionButtons = Array.from(document.querySelectorAll("button[data-action]"));

const storage = {
  async get(keys) {
    return new Promise((resolve, reject) => {
      try {
        chrome.storage.sync.get(keys, (result) => {
          const error = chrome.runtime.lastError;
          if (error) {
            reject(new Error(error.message));
          } else {
            resolve(result);
          }
        });
      } catch (error) {
        reject(error);
      }
    });
  },
  async set(items) {
    return new Promise((resolve, reject) => {
      try {
        chrome.storage.sync.set(items, () => {
          const error = chrome.runtime.lastError;
          if (error) {
            reject(new Error(error.message));
          } else {
            resolve();
          }
        });
      } catch (error) {
        reject(error);
      }
    });
  },
};

init();

function init() {
  restoreSettings();
  wireAutosave(apiKeyInput, "apiKey");
  wireAutosave(focusInput, "focusPreferences");

  actionButtons.forEach((button) => {
    button.addEventListener("click", () => handleGenerate(button.dataset.action));
  });

  setStatus("Ready when you are! Highlight text for a tighter focus.");
}

async function restoreSettings() {
  try {
    const stored = await storage.get(["apiKey", "focusPreferences"]);
    if (stored.apiKey) {
      apiKeyInput.value = stored.apiKey;
    }
    if (stored.focusPreferences) {
      focusInput.value = stored.focusPreferences;
    }
  } catch (error) {
    console.warn("Unable to restore settings", error);
  }
}

function wireAutosave(input, key) {
  let timeoutId;
  const persist = () => {
    const value = input.value.trim();
    storage.set({ [key]: value }).catch((error) => {
      console.warn(`Failed to save ${key}`, error);
    });
  };

  input.addEventListener("change", persist);
  input.addEventListener("blur", persist);
  input.addEventListener("input", () => {
    clearTimeout(timeoutId);
    timeoutId = setTimeout(persist, 400);
  });
}

async function handleGenerate(action) {
  const apiKey = apiKeyInput.value.trim();
  if (!apiKey) {
    setStatus("Add your Gemini API key to get started.", { error: true });
    apiKeyInput.focus();
    return;
  }

  try {
    setButtonsDisabled(true);
    setStatus("Collecting the page context...", { loading: true });
    const focus = focusInput.value.trim();
    const contextInfo = await collectPageContext();

    if (!contextInfo.text) {
      throw new Error("Couldn't read any text on this page. Try selecting the content first.");
    }

    showContext(contextInfo);

    setStatus("Talking with Gemini Flash...", { loading: true });
    const prompt = buildPrompt(action, contextInfo, focus);
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

function buildPrompt(action, contextInfo, focus) {
  const { text, truncated, usedSelection } = contextInfo;
  const focusLine = focus ? `\nLearner goals or focus areas: ${focus}` : "";
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
Use the context provided to craft the requested study aid.${selectionNote}${truncationNote}${focusLine}

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
    throw new Error("Gemini returned an empty response. Try again or refine your focus notes.");
  }

  return text;
}
