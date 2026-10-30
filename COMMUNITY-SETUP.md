# Switch on WaveRise Explore (ringtones & wallpapers)

Explore lets people sign in, upload ringtones and wallpapers, and like, share, download and follow.
It runs on Supabase's free plan. Until you finish step 4, the site works exactly as before and Explore stays hidden.

You need about 30 minutes. Replace `your-site.vercel.app` below with your real Vercel address.

## 1. Create a free Supabase project
1. Go to https://supabase.com, sign up (free), and click **New project**.
2. Name it `waverise`, choose a strong database password (save it), and pick the region **Mumbai** (closest to India).
3. Wait about 2 minutes until the project is ready.

## 2. Set up the database
1. Open `supabase-setup.sql` (in this folder). Near the top, find
   `insert into public.admins (email) values ('instavault8@gmail.com')`.
   Put the email you will sign in with (your Google account) there. You can add more lines for more admins.
2. In Supabase open **SQL Editor → New query**, paste the whole file, and click **Run**. It should say "Success".
   It is safe to run again later, for example after a WaveRise update.

## 3. Allow your website to sign people in
In Supabase open **Authentication → URL Configuration**:
- **Site URL:** `https://your-site.vercel.app`
- **Redirect URLs:** add `https://your-site.vercel.app/**`

## 4. Put your keys in the site
1. In Supabase click **Connect** at the top (or **Settings → API Keys**) and copy:
   - the **Project URL** (looks like `https://abcd1234.supabase.co`)
   - the **Publishable key** (starts with `sb_publishable_`), or the older **anon public** key
2. Open `public/config.js` and fill them in:
   ```js
   window.SUPABASE_URL = "https://abcd1234.supabase.co";
   window.SUPABASE_ANON_KEY = "sb_publishable_...";
   ```
   Both are meant to be public. The database rules from step 2 decide what anyone can do.
   Never put the **secret** / **service_role** key in the site.
3. Upload `public/index.html`, `public/glass.css` and `public/config.js` to GitHub and commit. After Vercel redeploys, **Explore** appears in the bottom bar.

## 5. Turn on "Continue with Google"
1. Go to https://console.cloud.google.com, create a project called `WaveRise`, then open **Google Auth Platform**.
2. **Branding:** app name `WaveRise`, your support email, and your logo if you like.
3. **Audience:** choose **External**. When everything works, click **Publish app** so anyone can sign in (in Testing mode only listed test users can).
4. **Data Access:** add the `openid` scope (email and profile are already there).
5. **Clients → Create client:** type **Web application**.
   - **Authorized JavaScript origins:** `https://your-site.vercel.app`
   - **Authorized redirect URIs:** the **Callback URL** shown in Supabase under **Authentication → Sign In / Providers → Google** (it looks like `https://abcd1234.supabase.co/auth/v1/callback`).
6. Copy the **Client ID** and **Client secret** into that Supabase Google page, switch Google **on**, and save.

## 6. (Optional) Turn on "Email me a sign-in link" for everyone
Supabase's built-in email only sends to members of your own Supabase team, and only 2 emails an hour.
Until you connect an email service, visitors see "Email sign-in isn't switched on yet. Please use Continue with Google."
To fix it for free with Brevo:
1. Sign up at https://www.brevo.com and verify a sender email address.
2. In Brevo, create an **SMTP key** (SMTP & API → SMTP).
3. In Supabase open **Authentication → Emails → SMTP Settings**, switch on custom SMTP and enter:
   host `smtp-relay.brevo.com`, port `587`, username = your Brevo login email, password = the SMTP key, and your verified sender email.

## 7. You are the admin
Sign in with the email you put in step 2, open your profile, and tap **Admin**. You'll see uploads that were reported.
An upload is hidden automatically after 3 reports. **Restore** brings it back, **Delete** removes it and its files.
You can also delete any upload from its own page.

## Free plan limits (Supabase, 2026)
- 1 GB of files: roughly 1,500 wallpapers or 3,000 ringtones.
- 5 GB of downloads a month.
- 50,000 signed-in users a month.
- A free project **pauses after a week with no visits**. Open the Supabase dashboard and click **Restore** if that happens.

## Built-in safety
- People can only change their own profile and uploads, and only put files in their own folder.
- Likes, downloads, followers and reports are counted by the database, so nobody can fake them.
- 20 uploads per person per day. Ringtones up to 60 seconds; files up to 10 MB; audio and pictures only.
- Wallpapers are re-saved on the phone before upload, which removes location and camera details.
- Emails are never shown. Anyone can delete their account (and everything they uploaded) from their profile.
