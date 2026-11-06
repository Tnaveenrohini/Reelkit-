# Moving WaveRise to Google Cloud Run

The repository has a `Dockerfile`. Google Cloud builds it on every push to `main` and runs the site on Cloud Run.
Everything below is done in the Google Cloud console (console.cloud.google.com). Never paste keys or passwords into chats.

## 1. Project and billing
1. Create a project, for example `waverise`.
2. Billing → link a billing account (a card is required, even for the free tier).
3. Billing → Budgets & alerts → create a budget of ₹500 per month with email alerts at 50%, 90% and 100%.

## 2. Create the service
Cloud Run → **Deploy container** → **Service** → **Continuously deploy from a repository (source or function)** → **Set up with Cloud Build**.

| Setting | Value |
|---|---|
| Repository | GitHub → `Tnaveenrohini/Reelkit-` (install the Google Cloud Build app on GitHub when asked) |
| Branch | `^main$` |
| Build type | Dockerfile, source location `/Dockerfile` |
| Service name | `waverise` |
| Region | `asia-east1` (Taiwan): lower price tier and supports custom domains |
| Authentication | Allow public access (unauthenticated) |
| Billing | Request-based |
| Scaling | Minimum instances 0, maximum instances 3 |
| Container port | 8080 |
| Memory / CPU | 1 GiB / 1 CPU |
| Request timeout | 300 seconds |
| Maximum concurrent requests | 20 |

**Variables & secrets**: copy every environment variable from Vercel → Settings → Environment Variables, with the same names and values:
`ADMIN_EMAILS`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `SESSION_SECRET`, `KV_REST_API_URL`, `KV_REST_API_TOKEN`, `CRON_SECRET`,
and any of `GEMINI_API_KEY`, `GEMINI_MODEL`, `ADSENSE_CLIENT`, `ADSENSE_SLOT`, `SOON_SITES`, `IG_DOC_ID`, `BLOCKED_LINKS`, `HELP_DAILY_LIMIT` you use.
Add `SITE_URL` = `https://waverise.in`.

Create. The first build takes about 5 minutes. Open the `https://waverise-….run.app` address it shows and check the home page, a photo tool and a video link.

## 3. Daily SEO check (replaces Vercel Cron)
Cloud Scheduler → Create job:
- Frequency `0 3 * * *`, time zone UTC
- Target HTTP, URL `https://waverise.in/api/cron/seo-check`, method GET
- Header `Authorization` = `Bearer ` followed by your CRON_SECRET value

## 4. Point waverise.in to Google
1. Cloud Run → **Manage custom domains** → Add mapping → service `waverise` → domain `waverise.in` (verify in Search Console if asked). Repeat for `www.waverise.in`.
2. Google shows DNS records (A and AAAA for `waverise.in`, CNAME `ghs.googlehosted.com` for `www`).
3. At your domain registrar, remove the Vercel records (A `76.76.21.21`, CNAME `cname.vercel-dns.com`) and add Google's records.
   If the domain uses Vercel's nameservers, first switch nameservers back to your registrar.
4. The HTTPS certificate is issued automatically, usually within 15 minutes to a few hours.

## 5. After it works
- Admin sign-in keeps working, because the domain stays the same.
- Keep the Vercel project for a few days as a backup, then remove the domain from it.
- Check Billing → Reports weekly for the first month. Video downloads count as outgoing data, which is charged after the free amount.
