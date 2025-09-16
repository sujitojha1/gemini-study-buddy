# Gemini Flashcards Tutor

Gemini Flashcards Tutor is a Chrome extension that turns any article, research paper, or video transcript into a personalized study guide powered by Google Gemini Flash 2.0. Highlight the part you care about or let the extension read the entire page to instantly receive summaries, flashcards, and self-check quizzes tailored to your learning goals.

## Features

- **One-click summaries** – distill complex pages into digestible bullet points and actionable takeaways.
- **AI-generated flashcards** – create 6-10 question/answer pairs in Markdown that you can copy into your favourite spaced repetition tool.
- **Quick quizzes** – produce five multiple-choice questions with explanations to reinforce understanding.
- **Focus-aware prompts** – optionally add study goals (exam, certification, audience, etc.) or highlight a passage to steer the response.
- **Local settings** – your Gemini API key and focus preferences are saved securely with `chrome.storage.sync` on your Google account.

## Requirements

- Google Chrome 115+ (Manifest V3 support).
- A Google account with access to [Google AI Studio](https://aistudio.google.com/) and a Gemini API key with access to Gemini Flash 2.0.

## Installation

1. Clone or download this repository and keep the `extension/` folder intact.
2. Open Google Chrome and navigate to `chrome://extensions`.
3. Enable **Developer mode** in the top-right corner.
4. Click **Load unpacked** and select the `extension/` directory from this project.
5. The Gemini Flashcards Tutor icon will appear in your toolbar. Pin it for quick access.

## Usage

1. Open a webpage, PDF (viewed in Chrome), or YouTube transcript that you want to study.
2. (Optional) Highlight a section to prioritize that text. Otherwise the full page will be analysed.
3. Click the Gemini Flashcards Tutor icon.
4. Paste your Gemini API key the first time you use the extension—it will be stored in Chrome Sync for future sessions.
5. Add any focus notes (e.g., “focus on exam-style questions” or “teach this to beginners”).
6. Choose **Generate Summary**, **Generate Flashcards**, or **Generate Quiz**.
7. Review the generated study aids directly in the popup and copy them into your notes or study app.

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

Enjoy faster studying with Gemini Flashcards Tutor! 🚀
