# WaveRise Assistant

What it does while you are away:

| Part | Where |
|---|---|
| Talks to visitors like a person (Telugu, Hindi, Tamil, English, also "Tenglish") | WaveRise Chat and the Help button on every page |
| Notes every error: server crashes, page (browser) errors, failed downloads, daily site check | Admin → **Error log** |
| Your own assistant: ask "ఈరోజు site ela undi?" and get a report | Admin → **Assistant** |
| A morning report, written after the daily site check (about 8:30 am IST) | Admin → **Assistant** → Today's report |
| Drafts replies to Contact messages; you edit, then send by email or WhatsApp | Admin → **Messages** → Draft a reply |

## Switch on the talking part (free)
1. Open https://aistudio.google.com/apikey and sign in with Google.
2. **Create API key** and copy it. Keep it private; don't paste it in any chat.
3. Vercel → reelkit project → **Settings → Environment Variables** → add `GEMINI_API_KEY` = your key (all environments).
4. **Deployments** → newest → **⋯** → **Redeploy**.

The error log works without the key. Without the key, the visitor chat keeps using its built-in answers.
The key stays on the server; browsers never see it. Free-quota limits: visitors get up to 20 questions per 10 minutes,
and the site stops asking the AI after 900 visitor questions a day (`HELP_DAILY_LIMIT`), so the free quota isn't used up.
Optional: `GEMINI_MODEL` picks a specific Gemini model.
