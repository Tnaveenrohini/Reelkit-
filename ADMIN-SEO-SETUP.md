# WaveRise – Admin SEO & Google Search setup

The admin area lives at **https://www.waverise.in/admin**. Visitors never see it. It is not linked anywhere on the site, robots.txt keeps it out of search results, and the server refuses every admin page and API call unless the visitor is signed in with a Google account listed in `ADMIN_EMAILS`.

---

## 1. Files

| File | Status | What it does |
|---|---|---|
| `app.py` | changed | Uses the new modules. The old home-page and SEO code was replaced by 4 lines. Every downloader and tool is unchanged. |
| `wr_store.py` | new | Small client for the Upstash Redis database (settings, overrides, logs). |
| `wr_seo.py` | new | Public SEO: renders each page with its own title, description, canonical, Open Graph, Twitter card and structured data. Also builds `sitemap.xml` and `robots.txt`, and handles redirects and the 404 log. |
| `wr_admin.py` | new | Admin sign-in (Google OAuth + PKCE), sessions, CSRF, rate limits, audit log, admin API, Search Console and Analytics APIs, and the SEO health monitor. |
| `templates/index.html` | new (moved) | The website page, moved from `public/index.html` so the server can add the verification tag and your SEO edits. |
| `templates/admin.html`, `admin.css`, `admin.js` | new | The admin dashboard. They are only sent to signed-in admins. |
| `requirements.txt` | changed | Adds `cryptography`, which encrypts the stored Google key. |
| `vercel.json` | new | Bundles `templates/` with the server code and runs the daily SEO check (03:00 UTC). |
| `public/index.html` | **DELETE** | If it stays, Vercel keeps serving the old static page and your SEO settings won't reach the home page. |

Everything else in `public/` (CSS, JS, icons, `og-image.jpg`) stays as it is.

---

## 2. Environment variables (Vercel → Project → Settings → Environment Variables, “Production”)

| Name | Value |
|---|---|
| `SITE_URL` | `https://www.waverise.in` |
| `ADMIN_EMAILS` | Your Google email, e.g. `you@gmail.com`. For more than one admin, separate with commas. |
| `SESSION_SECRET` | 64 random characters. Generate at random. Never reuse, never share. (Changing it signs everyone out and needs Google to be reconnected.) |
| `GOOGLE_CLIENT_ID` | From step 4. |
| `GOOGLE_CLIENT_SECRET` | From step 4. It stays on the server and is never sent to the browser. |
| `CRON_SECRET` | 32+ random characters. Vercel sends it automatically to the daily check. |
| `KV_REST_API_URL`, `KV_REST_API_TOKEN` | Added automatically when you connect Upstash Redis (step 3). `UPSTASH_REDIS_REST_URL` / `UPSTASH_REDIS_REST_TOKEN` also work. |

To make a random secret on any computer, run `python3 -c "import secrets;print(secrets.token_urlsafe(48))"`. Or use a password manager's generator set to 64 characters.

If a variable is missing, `/admin` shows a “Setup needed” page listing what to add. The public site keeps working either way.

---

## 3. Database (free)

1. Vercel → your project → **Storage** → **Create Database** → **Upstash for Redis** (Marketplace) → Free plan.
2. Connect it to the WaveRise project for **Production** (and Preview if you like).
3. Redeploy. The `KV_*` variables appear on their own.

What it stores: your settings and SEO edits, sessions, the 404 log, redirects, the audit log, health reports and the encrypted Google key. The public site reads it with a 30-second cache and CDN caching, so normal traffic stays far inside the free limits.

---

## 4. Google Cloud OAuth client (free)

1. Open https://console.cloud.google.com → create a project called “WaveRise Admin”.
2. **APIs & Services → Library** → enable:
   - **Google Search Console API**
   - **Google Analytics Data API**
   - **Google Analytics Admin API**
3. **Google Auth Platform → Branding / OAuth consent screen**:
   - User type: **External**.
   - App name: WaveRise Admin. Support email: yours.
   - Authorised domain: `waverise.in`.
4. **Data access (scopes)** → add:
   - `openid`
   - `.../auth/userinfo.email`
   - `.../auth/webmasters`
   - `.../auth/analytics.readonly`
5. **Audience** → **Publish app** (“In production”).
   - While the app is in “Testing”, Google ends the connection after 7 days.
   - Because only you use it, you don't need Google's app review. On first connect you'll see “Google hasn't verified this app”. Choose **Advanced → Go to WaveRise Admin**.
6. **Clients → Create client → Web application**:
   - Authorised redirect URI: `https://www.waverise.in/admin/oauth/callback`
   - Copy the **Client ID** and **Client secret** into Vercel (step 2).

---

## 5. Deploy (GitHub web upload)

1. In the GitHub repo, **delete `public/index.html`** (open it → ⋯ → Delete file → Commit).
2. **Add file → Upload files** and drag in:
   - `app.py`
   - `wr_store.py`, `wr_seo.py`, `wr_admin.py`
   - `requirements.txt`, `vercel.json`, `ADMIN-SEO-SETUP.md`
   - the whole `templates` folder (drag the folder so the 4 files land in `templates/`)

   Then commit.
3. Vercel → **Settings → Domains**:
   - `www.waverise.in` is the main domain.
   - `waverise.in` redirects to it (308).
4. Vercel deploys automatically. Wait until the deployment is **Ready**.

---

## 6. Search Console verification and first steps

1. Go to https://www.waverise.in/admin → **Sign in with Google**, using the account in `ADMIN_EMAILS`.
2. **Settings → Connect Google**. Use the Google account that owns (or will own) WaveRise in Search Console and Analytics.
3. Verify ownership at https://search.google.com/search-console → **Add property**. Pick one option.
   - **Domain property (recommended):**
     - Enter `waverise.in`.
     - Copy the TXT record `google-site-verification=…`.
     - At your domain registrar's DNS, add a record: type **TXT**, host **@**, with that value.
     - Click **Verify**. DNS can take minutes to a day.
   - **URL-prefix property:**
     - Enter `https://www.waverise.in/` and choose **HTML tag**.
     - Copy the code (or the whole `<meta>` tag) into **Admin → Settings → HTML tag verification code** → Save.
     - Wait 5 minutes, then click **Verify**.
4. **Admin → Settings → Properties for reports**:
   - Pick the Search Console property.
   - Optional: pick the Analytics property, and add your GA4 Measurement ID `G-…` to start tracking.
   - Save.
5. **Sitemap Manager → Submit to Google.** This uses the official Search Console sitemaps API.
6. **SEO Health → Run check now.** Fix anything red.
7. **Indexing Monitor → Check all pages with Google.** This uses the official URL Inspection API (Google allows 2,000 checks a day).

Google decides when pages are indexed, usually days to weeks for a new site. Nothing here (or anywhere) can force instant indexing. WaveRise does not use spam indexing services or unofficial indexing APIs.

---

## 7. Security

- **Sign-in:** Google OAuth on the server, with PKCE, a state cookie, and checks on the ID token's issuer, audience, expiry and email verification. Only emails in `ADMIN_EMAILS` get a session. Refused sign-ins are logged.
- **Sessions:** random ID in an `HttpOnly`, `Secure`, `SameSite=Lax` cookie. Stored hashed in the database, 8-hour lifetime, ended on sign-out.
- **Every admin API call** checks the session on the server.
- **Every change** also needs the session's CSRF token and a same-site `Origin`.
- **Rate limits:** sign-in 20 per 10 minutes per IP, API 240 per minute, SEO check 20 per hour, Google inspection 30 runs per hour.
- **Input validation:**
  - Titles and descriptions: length limits and no duplicates.
  - Links: `https://` or `/path` only.
  - robots.txt: only safe rules, and never `Disallow: /`.
  - JSON-LD: checked, and `</script>` is blocked.
  - Redirects: no reserved paths, no live pages, no loops.
- **Google key:** the refresh token is AES-GCM encrypted in the database. The client secret exists only in Vercel's environment variables.
- **Admin pages and API** send `noindex`, `no-store`, a strict Content-Security-Policy and `X-Frame-Options: DENY`.
- **Audit log:** every sign-in, refusal and change, with time, account and IP. See Admin → Security Logs.

---

## 8. Production testing checklist

Do these after deploying. Tick each one.

**As a visitor (private/incognito window):**
- [ ] https://www.waverise.in loads and every tool works as before.
- [ ] `https://waverise.in` redirects to `https://www.waverise.in`.
- [ ] View source on the home page and on 2 tool pages: each has its own `<title>`, description, `rel="canonical"` pointing at itself, `og:` tags, `twitter:` tags and `application/ld+json`.
- [ ] https://www.waverise.in/sitemap.xml opens as XML. It lists the public pages on `https://www.waverise.in/…` and contains no `/admin` or `/api`.
- [ ] https://www.waverise.in/robots.txt shows `Allow: /`, `Disallow: /api/`, `Disallow: /admin` and the `Sitemap:` line, and does **not** contain a bare `Disallow: /`.
- [ ] https://www.waverise.in/admin shows only “Sign in with Google”.
- [ ] https://www.waverise.in/api/admin/state answers **401**.
- [ ] https://www.waverise.in/admin/app.js answers **401**.
- [ ] Signing in with a Google account **not** in `ADMIN_EMAILS` shows “This Google account isn’t a WaveRise admin”.
- [ ] A made-up address (e.g. `/test-404`) shows the WaveRise “Page not found” page.

**As admin:**
- [ ] Sign in works, and the top bar shows Database connected.
- [ ] Settings → Connect Google works and shows Connected.
- [ ] Search Console shows clicks and impressions. A brand-new property shows none for the first 2–3 days.
- [ ] Sitemap Manager: Valid XML, and every page shows Included. Submit to Google works.
- [ ] Meta Tags Manager:
  - Edit one page's title → Save.
  - Within about 5 minutes its source shows the new title.
  - Reset to default.
- [ ] Redirects: add `/test-old` → `/`. Visiting `/test-old` redirects. Delete it.
- [ ] 404 Errors lists `/test-404`.
- [ ] SEO Health → Run check now: score shown, 0 errors.
- [ ] Indexing Monitor → Check: Google's answer is shown for each page.
- [ ] Security Logs lists your sign-in and changes.
- [ ] Sign out → `/admin` asks to sign in again.
- [ ] Next day, Vercel → Settings → **Cron Jobs** shows `/api/cron/seo-check` ran, and SEO Health says “checked … h ago”.

---

## 9. Troubleshooting

- **`/admin` says “Setup needed”:** add the listed variables, then redeploy.
- **“No database”:** connect Upstash (step 3) and redeploy.
- **Google: `redirect_uri_mismatch`:** the redirect URI in Google Cloud must be exactly `https://www.waverise.in/admin/oauth/callback`, and `SITE_URL` must be `https://www.waverise.in`.
- **“Google didn’t send a long-term key”:** remove WaveRise Admin at myaccount.google.com → Security → Third-party access, then connect again.
- **“The Google connection expired”:**
  - The app was still in Testing (step 4.5), or access was removed.
  - Publish the app, then Reconnect.
- **Google API error 403:** the connected Google account isn't an owner or user of that Search Console or Analytics property.
- **Home page doesn't show the verification tag:** `public/index.html` is still in GitHub. Delete it.
