const MODEL = "gemini-2.0-flash";
const LOCAL_API_BASE = "http://127.0.0.1:8000";
const LOCAL_GENERATE_ENDPOINT = `${LOCAL_API_BASE}/generate`;

const MAX_CONTEXT_LENGTH = 6000;

const statusEl = document.getElementById("status");
const resultSection = document.getElementById("resultSection");
const resultEl = document.getElementById("result");
const loadingTemplate = document.getElementById("loadingTemplate");
const flashcardButton = document.getElementById("flashcardBtn");

init();

function init() {
  flashcardButton.addEventListener("click", handleGenerate);
  setStatus("Highlight what matters or just run it on the full page.");
}

async function handleGenerate() {
  try {
    setButtonsDisabled(true);
    setStatus("Collecting the page context...", { loading: true });
    const contextInfo = await collectPageContext();

    if (!contextInfo.text) {
      throw new Error("Couldn't read any text on this page. Try selecting the content first.");
    }

    setStatus("Talking with Gemini...", { loading: true });
    const prompt = buildPrompt(contextInfo);
    const cardCount = 5;
    const payload = await callGemini(prompt, cardCount);
    showResult(payload, contextInfo, cardCount);
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
  flashcardButton.disabled = disabled;
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

function buildPrompt(contextInfo) {
  const { text, truncated, usedSelection } = contextInfo;
  const selectionNote = usedSelection
    ? "\nThe learner highlighted specific text. Prioritize the highlighted material while still using broader context when relevant."
    : "";
  const truncationNote = truncated
    ? "\nThe context was truncated for length. Respond using only the provided portion."
    : "";

  return `Create high-quality study flashcards for a learner using the material below.
Each card should cover a single concept, definition, or application in a way that encourages active recall.
${selectionNote}${truncationNote}

Context:
"""
${text}
"""`;
}

async function callGemini(prompt, cardCount) {
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
        flashcard_count: cardCount,
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

  if (!payload || typeof payload !== "object") {
    throw new Error("Local API returned an unexpected response.");
  }

  return payload;
}

function showResult(payload, contextInfo, cardCount) {
  resultEl.innerHTML = "";
  resultEl.classList.remove("has-flashcards");

  const cards = normalizeFlashcards(payload?.cards);
  if (!cards.length) {
    resultEl.textContent = "Gemini did not return any flashcards. Try again with a smaller selection.";
    resultSection.hidden = false;
    renderMetadata(payload, contextInfo, cardCount);
    return;
  }

  resultEl.classList.add("has-flashcards");
  renderFlashcards(cards);
  renderMetadata(payload, contextInfo, cardCount);
  resultSection.hidden = false;
}

function normalizeFlashcards(cardsObject) {
  if (!cardsObject || typeof cardsObject !== "object") {
    return [];
  }

  return Object.entries(cardsObject)
    .map(([id, value]) => ({
      id,
      front: toCleanString(value?.front),
      back: toCleanString(value?.back),
    }))
    .filter((card) => card.front);
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

  const dotButtons = [];

  const updateCard = (index) => {
    currentIndex = index;
    const { front, back, id } = cards[index];
    const frontHtml = escapeHtml(front).replace(/\n/g, "<br />");
    const backHtml = escapeHtml(back)
      .replace(/\n{2,}/g, "<br /><br />")
      .replace(/\n/g, "<br />");

    card.classList.remove("is-flipped");
    card.setAttribute("aria-pressed", "false");
    card.innerHTML = `
      <div class="flashcard-inner">
        <div class="flashcard-face flashcard-front">
          <span class="flashcard-label">${escapeHtml(id)}</span>
          ${frontHtml}
        </div>
        <div class="flashcard-face flashcard-back">${backHtml}</div>
      </div>
    `;

    dotButtons.forEach((dot, dotIndex) => {
      const isActive = dotIndex === index;
      dot.classList.toggle("is-active", isActive);
      dot.setAttribute("aria-pressed", isActive ? "true" : "false");
    });

    prevButton.disabled = index === 0;
    nextButton.disabled = index === cards.length - 1;
  };

  cards.forEach((cardData, index) => {
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

function renderMetadata() {}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function toCleanString(value) {
  if (value === null || value === undefined) {
    return "";
  }
  return String(value).trim();
}
