// Local FastAPI endpoint that proxies calls to Gemini.
const MODEL = "gemini-2.0-flash";
const LOCAL_API_BASE = "http://127.0.0.1:8000";
const LOCAL_GENERATE_ENDPOINT = `${LOCAL_API_BASE}/generate`;

// Limit the text we send so the backend stays within model token limits.
const MAX_CONTEXT_LENGTH = 6000;

const statusEl = document.getElementById("status");
const resultSection = document.getElementById("resultSection");
const resultEl = document.getElementById("result");
const metadataEl = document.getElementById("metadata");
const loadingTemplate = document.getElementById("loadingTemplate");
const flashcardButton = document.getElementById("flashcardBtn");

init();

// Wire up the popup UI once the DOM references are ready.
function init() {
  flashcardButton.addEventListener("click", handleGenerate);
  setStatus("Highlight what matters or just run it on the full page.");
}

async function handleGenerate() {
  // Main workflow: gather context, call the API, and render the cards.
  try {
    setButtonsDisabled(true);
    setStatus("Collecting the page context...", { loading: true });
    const contextInfo = await collectPageContext();

    if (!contextInfo.text) {
      throw new Error("Couldn't read any text on this page. Try selecting the content first.");
    }

    setStatus("Talking with Gemini...", { loading: true });
    const pageContextPayload = buildPageContextPayload(contextInfo);
    const cardCount = 5;
    const payload = await callGemini(pageContextPayload, cardCount);
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
  // Find the focused tab so we can inspect its contents.
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) {
    throw new Error("Couldn't find the active tab.");
  }

  try {
    // Run a single isolated script in the page to pull the selection/body text.
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

function buildPageContextPayload(contextInfo) {
  // Prepare a structured payload of the extracted page content for the backend.
  const { text, truncated, usedSelection } = contextInfo;

  return {
    text,
    truncated,
    used_selection: usedSelection,
  };
}

async function callGemini(pageContextPayload, cardCount) {
  // Delegate to the FastAPI service; it proxies the Gemini request.
  let response;
  try {
    response = await fetch(LOCAL_GENERATE_ENDPOINT, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        page_context: pageContextPayload,
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
  // Reset the results pane before rendering the new content.
  resultEl.innerHTML = "";
  resultEl.classList.remove("has-flashcards");
  clearMetadata();

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
  // Transform the keyed object into a clean list the UI can consume.
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
  // Build a simple carousel that flips cards and supports basic navigation.
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

function renderMetadata(payload, contextInfo) {
  if (!metadataEl) {
    return;
  }

  clearMetadata();

  const rating = typeof payload?.content_rating === "number" ? payload.content_rating : null;
  const rawHierarchy = toCleanString(payload?.information_hierarchy);
  const { hierarchy, jobNote } = parseInformationHierarchy(rawHierarchy);

  const selectionNotes = [];
  if (contextInfo?.usedSelection) {
    selectionNotes.push("Generated from your selection.");
  }
  if (contextInfo?.truncated) {
    selectionNotes.push(`Context truncated to ${MAX_CONTEXT_LENGTH} characters.`);
  }

  if (rating === null && !hierarchy && !jobNote && !selectionNotes.length) {
    return;
  }

  const metaWrapper = document.createElement("div");
  metaWrapper.className = "flashcard-meta";

  if (rating !== null) {
    const ratingCard = document.createElement("div");
    ratingCard.className = "flashcard-summary meta-card";

    const heading = document.createElement("h2");
    heading.textContent = "Learning Value";

    const ratingRow = document.createElement("div");
    ratingRow.className = "meta-rating-row";

    const ratingScore = document.createElement("span");
    ratingScore.className = "meta-rating-score";
    ratingScore.textContent = `${rating}/10`;

    const ratingDescription = document.createElement("span");
    ratingDescription.className = "meta-rating-description";
    ratingDescription.textContent = describeRating(rating);

    ratingRow.appendChild(ratingScore);
    ratingRow.appendChild(ratingDescription);

    ratingCard.appendChild(heading);
    ratingCard.appendChild(ratingRow);
    metaWrapper.appendChild(ratingCard);
  }

  if (hierarchy || jobNote) {
    const hierarchyCard = document.createElement("div");
    hierarchyCard.className = "flashcard-summary meta-card";

    const heading = document.createElement("h2");
    heading.textContent = "Information Hierarchy";

    const button = document.createElement("button");
    button.type = "button";
    button.className = "hierarchy-button";
    button.textContent = "View structure";
    button.addEventListener("click", () => showHierarchyModal(hierarchy, jobNote));

    hierarchyCard.appendChild(heading);
    hierarchyCard.appendChild(button);

    if (jobNote) {
      const noteEl = document.createElement("p");
      noteEl.className = "meta-note";
      noteEl.textContent = jobNote;
      hierarchyCard.appendChild(noteEl);
    }

    metaWrapper.appendChild(hierarchyCard);
  }

  if (selectionNotes.length) {
    const noteEl = document.createElement("p");
    noteEl.className = "flashcard-selection-note";
    noteEl.textContent = selectionNotes.join(" ");
    metaWrapper.appendChild(noteEl);
  }

  metadataEl.appendChild(metaWrapper);
  metadataEl.hidden = false;
}

function clearMetadata() {
  if (!metadataEl) {
    return;
  }
  metadataEl.innerHTML = "";
  metadataEl.hidden = true;
}

function describeRating(score) {
  if (score >= 8) {
    return "High depth content - great for learning.";
  }
  if (score >= 5) {
    return "Moderate depth - useful but could go deeper.";
  }
  if (score >= 1) {
    return "Light coverage - mostly surface-level info.";
  }
  return "No rating available.";
}

function parseInformationHierarchy(rawValue) {
  if (!rawValue) {
    return { hierarchy: "", jobNote: "" };
  }

  const marker = "note about job function:";
  const lowerValue = rawValue.toLowerCase();
  const markerIndex = lowerValue.lastIndexOf(marker);

  if (markerIndex === -1) {
    return { hierarchy: rawValue.trim(), jobNote: "" };
  }

  const hierarchy = rawValue.slice(0, markerIndex).trim();
  const jobNote = rawValue.slice(markerIndex).trim();
  return { hierarchy, jobNote };
}

let activeHierarchyModal = null;

function showHierarchyModal(hierarchyText, jobNote) {
  closeHierarchyModal();

  if (!hierarchyText && !jobNote) {
    return;
  }

  const overlay = document.createElement("div");
  overlay.className = "hierarchy-modal-overlay";

  const modal = document.createElement("div");
  modal.className = "hierarchy-modal";
  modal.setAttribute("role", "dialog");
  modal.setAttribute("aria-modal", "true");
  modal.setAttribute("aria-labelledby", "hierarchyModalTitle");
  modal.tabIndex = -1;

  const header = document.createElement("div");
  header.className = "hierarchy-modal-header";

  const title = document.createElement("h2");
  title.id = "hierarchyModalTitle";
  title.textContent = "Information Hierarchy";

  const closeButton = document.createElement("button");
  closeButton.type = "button";
  closeButton.className = "hierarchy-modal-close";
  closeButton.setAttribute("aria-label", "Close information hierarchy");
  closeButton.innerHTML = "&times;";

  header.appendChild(title);
  header.appendChild(closeButton);

  const body = document.createElement("div");
  body.className = "hierarchy-modal-body";

  if (hierarchyText) {
    const hierarchyContent = document.createElement("div");
    hierarchyContent.className = "hierarchy-modal-tree";
    hierarchyContent.innerHTML = formatHierarchyForModal(hierarchyText);
    body.appendChild(hierarchyContent);
  }

  if (jobNote) {
    const jobNoteEl = document.createElement("p");
    jobNoteEl.className = "hierarchy-modal-note";
    jobNoteEl.textContent = jobNote;
    body.appendChild(jobNoteEl);
  }

  modal.appendChild(header);
  modal.appendChild(body);
  overlay.appendChild(modal);
  document.body.appendChild(overlay);

  const onOverlayClick = (event) => {
    if (event.target === overlay) {
      closeHierarchyModal();
    }
  };

  const onClose = () => {
    closeHierarchyModal();
  };

  overlay.addEventListener("click", onOverlayClick);
  closeButton.addEventListener("click", onClose);

  requestAnimationFrame(() => {
    overlay.classList.add("is-visible");
    modal.focus();
  });

  document.addEventListener("keydown", handleHierarchyModalKeydown);
  activeHierarchyModal = overlay;
}

function closeHierarchyModal() {
  if (!activeHierarchyModal) {
    return;
  }
  activeHierarchyModal.remove();
  activeHierarchyModal = null;
  document.removeEventListener("keydown", handleHierarchyModalKeydown);
}

function handleHierarchyModalKeydown(event) {
  if (event.key === "Escape") {
    closeHierarchyModal();
  }
}

function formatHierarchyForModal(hierarchyText) {
  if (!hierarchyText) {
    return "<em>No hierarchy available.</em>";
  }

  return hierarchyText
    .split("\n")
    .map((line) => {
      const leadingWhitespaceMatch = line.match(/^\s*/);
      const leadingWhitespace = leadingWhitespaceMatch ? leadingWhitespaceMatch[0] : "";
      const indentLevel = leadingWhitespace.replace(/\t/g, "  ").length;
      const nbspIndent = "&nbsp;".repeat(indentLevel);
      const trimmedLine = line.trimStart();
      const bulletLine = trimmedLine.startsWith("- ") ? `• ${trimmedLine.slice(2)}` : trimmedLine;
      return `${nbspIndent}${escapeHtml(bulletLine)}`;
    })
    .join("<br />");
}

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
