# WaveRise Assistant

What it does while you are away:

| Part | Where |
|---|---|
| Talks to visitors like a person (Telugu, Hindi, Tamil, English, also "Tenglish") | WaveRise Chat and the Help button on every page |
| Notes every error: server crashes, page (browser) errors, failed downloads, daily site check | Admin → **Error log** |
| Your own assistant: ask "ఈరోజు site ela undi?" and get a report | Admin → **Assistant** |
| A morning report, written after the daily site check (about 8:30 am IST) | Admin → **Assistant** → Today's report |
| Drafts replies to Contact messages; you edit, then send by email or WhatsApp | Admin → **Messages** → Draft a reply |

## No key needed
Everything runs on WaveRise's own assistant (`wr_brain.py`): no API key, no outside AI, no cost, no limits.
It understands English, Telugu, Hindi and Tamil (in their own script or typed in English letters) and learns
from the site itself: the help answers, every tool's steps and FAQ, and the tool list. When you add a tool's
steps or FAQ in the Guide editor, the assistant knows them too.

Optional: if you ever add a Google Gemini key as `GEMINI_API_KEY` in Vercel, free-form questions are answered
by Gemini, and WaveRise's own assistant takes over whenever Gemini is busy.
