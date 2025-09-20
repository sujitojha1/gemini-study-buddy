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
  if (action !== "flashcards") {
    console.error(`Unsupported action requested: ${action}`);
    setStatus("This action is no longer available.", { error: true });
    return;
  }

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
    showResult(output, action);
    setStatus("Cards ready! Click a card to reveal the answer.");
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

function showResult(output, action) {
  resultEl.innerHTML = "";
  resultEl.classList.remove("has-flashcards");

  if (action === "flashcards") {
    const cards = parseFlashcards(output);
    if (cards.length > 0) {
      resultEl.classList.add("has-flashcards");
      renderFlashcards(cards);
    } else {
      resultEl.textContent = output;
    }
  } else {
    resultEl.textContent = output;
  }

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

  if (action !== "flashcards") {
    throw new Error("Unsupported action requested.");
  }

  const task = `Produce 6-10 concise flashcards formatted as Markdown.
For each card write a bold question line followed by an indented answer line that learners can quickly review.
Vary between concept, definition, and application questions.`;

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

function renderFlashcards(cards) {
  if (!cards.length) {
    return;
  }

  let currentIndex = 0;

  const carousel = document.createElement("div");
  carousel.className = "flashcard-carousel";

  const controls = document.createElement("div");
  controls.className = "flashcard-controls";

  const prevButton = document.createElement("button");
  prevButton.type = "button";
  prevButton.className = "flashcard-nav flashcard-nav-prev";
  prevButton.setAttribute("aria-label", "Previous flashcard");
  prevButton.innerHTML = "&#x2039;";

  const nextButton = document.createElement("button");
  nextButton.type = "button";
  nextButton.className = "flashcard-nav flashcard-nav-next";
  nextButton.setAttribute("aria-label", "Next flashcard");
  nextButton.innerHTML = "&#x203a;";

  const dots = document.createElement("div");
  dots.className = "flashcard-dots";

  const card = document.createElement("button");
  card.type = "button";
  card.className = "flashcard";
  card.setAttribute("aria-pressed", "false");
  card.setAttribute("aria-label", "Flip flashcard to reveal the answer");

  const dotButtons = [];

  const createFaceContent = (face, labelText, lines) => {
    face.innerHTML = "";

    const label = document.createElement("span");
    label.className = "flashcard-label";
    label.textContent = labelText;
    face.appendChild(label);

    const body = document.createElement("div");
    body.className = "flashcard-body";
    face.appendChild(body);

    const hasLines = Array.isArray(lines) && lines.length > 0;
    const contentLines = hasLines ? lines : ["No content provided."];

    let currentList = null;
    contentLines.forEach((line) => {
      const trimmed = line.trim();
      if (!trimmed) {
        currentList = null;
        const spacer = document.createElement("div");
        spacer.className = "flashcard-body-spacer";
        body.appendChild(spacer);
        return;
      }

      const bulletMatch = trimmed.match(/^•\s*(.+)$/);
      if (bulletMatch) {
        if (!currentList) {
          currentList = document.createElement("ul");
          body.appendChild(currentList);
        }
        const item = document.createElement("li");
        item.textContent = bulletMatch[1].trim();
        currentList.appendChild(item);
        return;
      }

      currentList = null;
      const paragraph = document.createElement("p");
      paragraph.textContent = trimmed;
      body.appendChild(paragraph);
    });
  };

  const updateCard = (index) => {
    currentIndex = index;
    const { question, answer, answerLines } = cards[index];

    const questionLines = question
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean);

    const answerContent = Array.isArray(answerLines)
      ? answerLines
      : answer
          .split(/\r?\n/)
          .map((line) => line.trim())
          .filter(Boolean);

    card.classList.remove("is-flipped");
    card.setAttribute("aria-pressed", "false");
    card.innerHTML = "";

    const inner = document.createElement("div");
    inner.className = "flashcard-inner";

    const frontFace = document.createElement("div");
    frontFace.className = "flashcard-face flashcard-front";
    createFaceContent(frontFace, "Question", questionLines.length ? questionLines : [question || "Untitled question"]);

    const backFace = document.createElement("div");
    backFace.className = "flashcard-face flashcard-back";
    createFaceContent(backFace, "Answer", answerContent.length ? answerContent : ["No answer provided."]);

    inner.appendChild(frontFace);
    inner.appendChild(backFace);
    card.appendChild(inner);

    dotButtons.forEach((dot, dotIndex) => {
      const isActive = dotIndex === index;
      dot.classList.toggle("is-active", isActive);
      dot.setAttribute("aria-pressed", isActive ? "true" : "false");
    });

    prevButton.disabled = index === 0;
    nextButton.disabled = index === cards.length - 1;
  };

  cards.forEach((_, index) => {
    const dot = document.createElement("button");
    dot.type = "button";
    dot.className = "flashcard-dot";
    dot.setAttribute("aria-label", `Go to flashcard ${index + 1}`);
    dot.setAttribute("aria-pressed", "false");
    dot.addEventListener("click", () => {
      updateCard(index);
    });
    dots.appendChild(dot);
    dotButtons.push(dot);
  });

  prevButton.addEventListener("click", () => {
    if (currentIndex > 0) {
      updateCard(currentIndex - 1);
    }
  });

  nextButton.addEventListener("click", () => {
    if (currentIndex < cards.length - 1) {
      updateCard(currentIndex + 1);
    }
  });

  card.addEventListener("click", () => {
    card.classList.toggle("is-flipped");
    card.setAttribute("aria-pressed", card.classList.contains("is-flipped") ? "true" : "false");
  });

  controls.appendChild(prevButton);
  controls.appendChild(dots);
  controls.appendChild(nextButton);

  carousel.appendChild(controls);
  carousel.appendChild(card);

  resultEl.appendChild(carousel);
  updateCard(0);
}

function parseFlashcards(markdown) {
  const lines = markdown.split(/\r?\n/);
  const cards = [];

  let currentQuestion = null;
  let answerLines = [];

  const flush = () => {
    if (!currentQuestion) {
      return;
    }
    const compactLines = answerLines
      .map((line) => line.trim())
      .filter((line, index, array) => {
        if (!line && index > 0 && array[index - 1] === "") {
          return false;
        }
        return true;
      });

    while (compactLines.length && compactLines[0] === "") {
      compactLines.shift();
    }
    while (compactLines.length && compactLines[compactLines.length - 1] === "") {
      compactLines.pop();
    }
    const answerText = compactLines.join("\n").trim();
    const fallback = answerText || "No answer provided.";
    const linesForFace = compactLines.length > 0 ? compactLines : [fallback];

    cards.push({
      question: currentQuestion,
      answer: fallback,
      answerLines: linesForFace,
    });
    currentQuestion = null;
    answerLines = [];
  };

  lines.forEach((line) => {
    const questionMatch = line.match(/^\*\*(.+?)\*\*/);
    if (questionMatch) {
      if (currentQuestion) {
        flush();
      }
      currentQuestion = cleanQuestion(questionMatch[1].trim());
      answerLines = [];
      return;
    }

    if (currentQuestion) {
      if (line.trim() === "") {
        if (answerLines.length > 0) {
          answerLines.push("");
        }
        return;
      }

      const trimmed = line.trim();
      const normalized = trimmed.replace(/^[-*]\s+/, "• ");
      answerLines.push(normalized);
    }
  });

  flush();

  return cards;
}

function cleanQuestion(text) {
  let cleaned = text;
  cleaned = cleaned.replace(/^(?:q\d+[:.)-]?\s*)/i, "");
  cleaned = cleaned.replace(/^\d+\s*[).:-]?\s*/, "");
  cleaned = cleaned.replace(/^(?:[-*•]\s*)/, "");
  return cleaned.trim();
}
