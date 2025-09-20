# Gemini Flashcards Tutor

Gemini Flashcards Tutor is a Chrome extension that turns any article, research paper, or video transcript into a personalized study guide powered by Google Gemini Flash 2.0. Highlight the part you care about or let the extension read the entire page to instantly receive summaries, flashcards, and self-check quizzes tailored to your learning goals.

## Features

- **One-click summaries** – distill complex pages into digestible bullet points and actionable takeaways.
- **AI-generated flashcards** – create 6-10 question/answer pairs in Markdown that you can copy into your favourite spaced repetition tool.
- **Quick quizzes** – produce five multiple-choice questions with explanations to reinforce understanding.
- **In-popup API key entry** – paste your Gemini API key directly into the popup; it only lives for the current session and never leaves your device.

## Requirements

- Google Chrome 115+ (Manifest V3 support).
- A Google account with access to [Google AI Studio](https://aistudio.google.com/) and a Gemini API key with access to Gemini Flash 2.0.

## Installation

1. Clone or download this repository and keep the `extension/` folder intact.
2. Open Google Chrome and navigate to `chrome://extensions`.
3. Enable **Developer mode** in the top-right corner.
4. Click **Load unpacked** and select the `extension/` directory from this project.
5. The Gemini Flashcards Tutor icon will appear in your toolbar. Pin it for quick access.
6. Open the extension popup and paste your Gemini API key when prompted. The key is only used locally while the popup remains open.

## Usage

1. Open a webpage, PDF (viewed in Chrome), or YouTube transcript that you want to study.
2. (Optional) Highlight a section to prioritize that text. Otherwise the full page will be analysed.
3. Click the Gemini Flashcards Tutor icon.
4. Paste your Gemini API key into the popup and click **Load key**. The key is kept in-memory only for that session.
5. Choose **Generate Summary**, **Generate Flashcards**, or **Generate Quiz**.
6. Review the generated study aids directly in the popup and copy them into your notes or study app.

> **Tip:** If the page is very long, the extension trims the context that is sent to Gemini. You can refine the focus by highlighting the most important section before generating content.

## File structure

```
extension/
├── manifest.json    # Chrome extension manifest (MV3)
├── popup.css        # Styling for the popup UI
├── popup.html       # Extension popup markup
└── popup.js         # Popup logic, Gemini API calls, and content extraction
```

## Privacy & limits

- The extension only sends text you explicitly request (highlighted selection or page contents) to the Gemini API. Nothing is stored on external servers.
- Gemini API usage is subject to your Google AI Studio quota and billing. Handle API keys carefully—treat them like passwords and rotate if needed.

## Development notes

- The extension uses `chrome.scripting.executeScript` to read page content on demand. Restricted pages such as the Chrome Web Store cannot be accessed.
- Responses are rendered as plain text to keep the popup lightweight. Paste them into a Markdown editor for richer formatting.
- To customize prompts or behaviour, edit `extension/popup.js` and reload the unpacked extension from `chrome://extensions`.

## Local FastAPI bridge

The repository includes a lightweight FastAPI app under `server/` that proxies requests to the Gemini API. Running it locally lets the Chrome extension call `http://127.0.0.1:8000/generate` instead of talking to Gemini directly.

1. Create a virtual environment of your choice inside `server/`.
2. Install dependencies: `pip install -r server/requirements.txt`.
3. Provide your Gemini key by either exporting `GEMINI_API_KEY` or copying `server/.env.example` to `server/.env` and filling in the value.
4. Start the server: `uvicorn app:app --reload` (run from the `server/` directory).
5. Load or reload the Chrome extension. The popup will forward prompts—and any key you paste—to the local service, which then reaches Gemini.

If the extension shows "Could not reach the local Gemini server," make sure the FastAPI process is running and listening on port 8000.

Enjoy faster studying with Gemini Flashcards Tutor! 🚀
