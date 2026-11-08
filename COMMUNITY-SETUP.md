# Switch on Ringtones & Wallpapers

People sign in with Google, upload ringtones and wallpapers, and like, share, download and follow.
It runs on WaveRise's own server code (`wr_comm.py`). There is no Supabase.

| What | Where it lives |
|---|---|
| Sign-in | Your existing Google client (the same one as admin sign-in) |
| Records (uploads, likes, follows, reports) | Your existing Upstash Redis database |
| Files (MP3s and pictures) | A **public Vercel Blob** store (free on Hobby: 1 GB stored, 10 GB downloads a month) |

Until all of it is set up, the pages at **waverise.in/ringtones** and **/wallpapers** say "coming soon" and the links stay hidden.

## 1. Create the file store (Vercel Blob)
1. In Vercel open your **reelkit** project → **Storage** → **Create** → **Blob**.
2. Name it `waverise-files` and choose **Public** access.
3. Connect it to the reelkit project (all environments). Vercel adds `BLOB_READ_WRITE_TOKEN` by itself.

## 2. Let Google send people back to WaveRise
In Google Cloud → **Google Auth Platform** → **Clients** → your **Web client**:
1. Under **Authorized redirect URIs**, add `https://waverise.in/api/auth/google/callback`. Keep the admin one too.
2. Save.
3. **Audience** → **Publish app**, so everyone can sign in (in Testing mode only listed test users can).

## 3. Deploy
In Vercel → **Deployments** → the newest one → **⋯** → **Redeploy**.
Ringtones and Wallpapers then appear in the Tools menu, the phone tools list and the home page.

These must already be set (admin sign-in uses them): `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `SESSION_SECRET`, `ADMIN_EMAILS`, and the Upstash `KV_REST_API_URL` / `KV_REST_API_TOKEN`.
You can delete `SUPABASE_URL` and `SUPABASE_ANON_KEY`; they are no longer used.

## 4. You are the admin
Every email in `ADMIN_EMAILS` is a community admin. Sign in on waverise.in/ringtones, open your profile and tap **Admin**.
You'll see uploads that were reported. An upload is hidden automatically after 3 reports. **Restore** brings it back, **Delete** removes it and its files.
You can also delete any upload from its own page.

## Limits built in
- Ringtones: MP3, M4A, AAC or OGG, up to 60 seconds and 4 MB.
- Wallpapers: resized on the phone to 1920 px (JPEG), with a small preview picture.
- 20 uploads a day per person. Emails are never shown.
