"""WaveRise Guides: original step-by-step articles, rendered on the server (wr_seo) inside the normal page.
Each guide: slug, category, title, description, read time, the tool it explains, sections of HTML (our own writing)."""
import html as _html

esc = lambda v: _html.escape(str(v), quote=True)
UPDATED = "2026-11-02"
UPDATED_TXT = "2 November 2026"
AUTHOR = "WaveRise Team"

CATS = [("forms", "Exam & job forms"), ("photos", "Photos"), ("whatsapp", "WhatsApp"), ("festivals", "Festivals")]
CAT_NAME = dict(CATS)
ICONS = {
    "forms": '<rect x="5" y="3" width="14" height="18" rx="2"/><circle cx="12" cy="10" r="2.6"/><path d="M8 18c.8-2 2.2-3 4-3s3.2 1 4 3"/>',
    "sign": '<path d="M4 17c3-1 4-4 6-4s3 3 6 3 3-2 4-3"/><path d="M4 7h16"/>',
    "photos": '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 16l-5-5-8 8"/>',
    "whatsapp": '<rect x="4" y="5" width="16" height="12" rx="2"/><path d="M9 21h6M12 17v4"/>',
    "festivals": '<path d="M12 3c2 3 3 5 3 7a3 3 0 0 1-6 0c0-2 1-4 3-7z"/><path d="M6 21h12M8 17h8"/>',
}

GUIDES = [
    {"slug": "resize-photo-20-50-kb-exam-forms", "cat": "forms", "icon": "forms", "read": 4, "tool": ("Photo MB to KB", "/photo-mb-to-kb"),
     "title": "How to resize a photo to 20–50 KB for exam forms",
     "desc": "Make your photo the size an online exam or job form asks for, on your phone, without losing a clear face. Free, no upload.",
     "lead": "Most online application forms reject photos that are too big. A phone photo is usually 2 to 5 MB, while many forms want 20 to 50 KB. Here is how to make your photo fit, on your phone, without losing a clear face.",
     "sections": [
         ("what", "1. What the form asks for",
          "<p>Every notification lists the photo rules. Before you start, write down three things:</p>"
          "<ul><li><b>File size</b>, for example “between 20 KB and 50 KB”.</li><li><b>File type</b>, usually JPG or JPEG.</li>"
          "<li><b>Pixels</b>, only if the form gives them, for example 200 × 230 px.</li></ul>"
          "<p>Many forms ask for a JPG photo between 20 and 50 KB, and a signature between 10 and 20 KB. These are only examples: the notification for your exam is always the rule to follow.</p>"),
         ("steps", "2. Step by step",
          "<ol><li>Open <a href=\"/photo-mb-to-kb\">Photo MB to KB</a> and tap <b>Choose photo</b>.</li>"
          "<li>Tap the size the form asks for, such as <b>Under 50 KB</b>. For any other size, tap <b>Type my own</b>.</li>"
          "<li>If the form gives a smallest size too, type it in <b>Smallest size</b>. If it gives exact pixels, type the width and height.</li>"
          "<li>Tap <b>Download</b> and upload the new file to your form.</li></ol>"
          "<p>WaveRise keeps the clearest picture that fits the size. The photo is made on your phone and is never uploaded to our server.</p>"),
         ("rejects", "3. If the form still rejects it",
          "<ul><li><b>Wrong type:</b> most forms want JPG. Photo MB to KB always saves a JPG.</li>"
          "<li><b>“File too small”:</b> the form has a minimum size. Type it in Smallest size and WaveRise fills the file up to it without changing the picture.</li>"
          "<li><b>Wrong dimensions:</b> type the exact width and height from the notification.</li>"
          "<li><b>Face not clear:</b> start from a well-lit photo taken against a plain wall. A sharper start gives a sharper result.</li></ul>"),
         ("tips", "4. Tips for a photo that passes",
          "<p>Use a recent photo with a plain light background, your face straight to the camera and both ears visible if the rules ask for it. Avoid filters and heavy edits. If the form also needs your name and the date under the photo, use <a href=\"/photo-with-name-and-date\">Photo with Name &amp; Date</a>.</p>"),
     ], "related": ["clean-signature-online-application", "passport-photo-size-print"]},

    {"slug": "clean-signature-online-application", "cat": "forms", "icon": "sign", "read": 3, "tool": ("Signature Maker", "/signature-resizer"),
     "title": "Make a clean signature for online applications",
     "desc": "Turn a phone photo of your signature into a clean, white-background image at the exact pixels and KB a form asks for.",
     "lead": "Bank, exam and job portals often ask for a scanned signature in a strict size. You don’t need a scanner: a phone photo and a few taps are enough.",
     "sections": [
         ("paper", "1. Sign on plain paper",
          "<p>Use plain white paper and a black or dark blue pen. Sign a little bigger than usual, in the middle of the page, and leave space around it. Avoid lined or coloured paper: the lines can show up in the final image.</p>"),
         ("photo", "2. Take a good photo",
          "<p>Hold your phone straight above the paper in good daylight. Make sure your hand or phone doesn’t throw a shadow across the signature, and that the whole signature is in focus.</p>"),
         ("make", "3. Make it the right size",
          "<ol><li>Open <a href=\"/signature-resizer\">Signature Maker</a> and choose your photo.</li>"
          "<li>Pick the size your form asks for. Bank exam forms often ask for 140 × 60 px and 10–20 KB; always check your notification.</li>"
          "<li>Choose black or blue ink and move the ink darkness slider if lines look thin.</li>"
          "<li>Download the signature and upload it to the form.</li></ol>"
          "<p>WaveRise makes the paper pure white, darkens the ink and crops the signature neatly. It all happens on your phone.</p>"),
         ("problems", "4. Common problems",
          "<ul><li><b>Grey background:</b> retake the photo in brighter light.</li><li><b>Broken lines:</b> move the ink darkness slider to the right.</li>"
          "<li><b>File too big or small:</b> choose <b>My own size</b> and type the pixels and KB range from the notification.</li></ul>"),
     ], "related": ["resize-photo-20-50-kb-exam-forms", "passport-photo-size-print"]},

    {"slug": "long-video-whatsapp-status", "cat": "whatsapp", "icon": "whatsapp", "read": 3, "tool": ("Status Splitter", "/whatsapp-status-splitter"),
     "title": "Post a long video on WhatsApp Status",
     "desc": "WhatsApp Status only takes short clips. Here is how to share a longer video in parts, in the right order.",
     "lead": "WhatsApp Status only accepts short video clips, so a longer video gets cut off. The fix is to split it into parts and post them one after another, so your contacts see the whole video in order.",
     "sections": [
         ("limit", "1. Why your video gets cut",
          "<p>Each Status clip has a time limit. Recent versions of WhatsApp allow up to about a minute, older versions less. Anything longer is trimmed, so the end of your video is lost.</p>"),
         ("own", "2. A video on your phone",
          "<p>When you add a long video from your gallery, WhatsApp shows a trimmer at the top. Post the first part, then add the same video again and choose the next part. It works, but it is slow for long videos.</p>"),
         ("split", "3. A public video with a link",
          "<ol><li>Copy the link of the public video.</li><li>Open <a href=\"/whatsapp-status-splitter\">Status Splitter</a>, paste the link and choose 15, 30 or 60-second parts.</li>"
          "<li>Tap <b>Send to WhatsApp</b> on part 1, then part 2, and so on, so they appear in order.</li></ol>"
          "<p>Only share videos you made or have permission to share.</p>"),
         ("small", "4. Make it quicker to send",
          "<p>Big files are slow on mobile data. <a href=\"/compress-video-for-whatsapp\">Make it small</a> makes a lighter MP4 that still looks good on a phone screen.</p>"),
     ], "related": ["resize-photo-20-50-kb-exam-forms", "diwali-greeting-own-photo"]},

    {"slug": "passport-photo-size-print", "cat": "photos", "icon": "photos", "read": 5, "tool": ("Passport Photo", "/passport-photo-maker"),
     "title": "Passport photo sizes and how to print them",
     "desc": "The common passport and visa photo sizes, how to make one at home with a white background, and how to print a full sheet cheaply.",
     "lead": "A studio isn’t the only way to get passport photos. With a good phone photo you can make the right size at home and print a whole sheet at any photo shop for a few rupees.",
     "sections": [
         ("sizes", "1. Common sizes",
          "<ul><li><b>35 × 45 mm</b>: used by many passport and visa applications, including India’s.</li>"
          "<li><b>2 × 2 inches (51 × 51 mm)</b>: used for US visas and passports.</li><li><b>Stamp size</b>: some local forms ask for a smaller photo.</li></ul>"
          "<p>Rules change and every office has its own, so always check the current photo rules for your application before printing.</p>"),
         ("take", "2. Take the photo",
          "<p>Stand in front of a plain light wall in daylight. Look straight at the camera with a neutral face, eyes open and hair away from your face. Ask someone to take the photo from about one metre away at eye level.</p>"),
         ("make", "3. Make it the right size",
          "<ol><li>Open <a href=\"/passport-photo-maker\">Passport Photo</a> and choose your photo.</li>"
          "<li>Pick the size. WaveRise finds your face, centres it and makes the background white.</li>"
          "<li>Download a single photo for online forms, or a print sheet.</li></ol>"),
         ("print", "4. Print a sheet",
          "<p>A 4 × 6 inch print holds 8 photos of 35 × 45 mm, and an A4 page about 30, both at 300 dpi. Ask the photo shop for a normal 4 × 6 print at actual size, with no cropping or “fit to page”, then cut along the lines.</p>"),
     ], "related": ["remove-photo-background-profile-picture", "resize-photo-20-50-kb-exam-forms"]},

    {"slug": "diwali-greeting-own-photo", "cat": "festivals", "icon": "festivals", "read": 2, "tool": ("Festival Frames", "/festival-frames"),
     "title": "Make a Diwali greeting with your own photo",
     "desc": "Send a personal Diwali wish with your own photo and name, ready for WhatsApp Status or a post. Free and made on your phone.",
     "lead": "Forwarded greetings all look the same. A greeting with your own photo and name feels personal, and it takes less than a minute.",
     "sections": [
         ("photo", "1. Pick a good photo",
          "<p>Choose a clear photo of yourself or your family, with faces well lit. Photos taken in daylight or near a lamp work best. A plain background makes the frame stand out.</p>"),
         ("make", "2. Make the greeting",
          "<ol><li>Open <a href=\"/festival-frames\">Festival Frames</a> and choose <b>Diwali</b>.</li>"
          "<li>Add your photo and type your name or your family’s name.</li><li>Pick Status or post size.</li>"
          "<li>Tap <b>Send to WhatsApp</b> or download the image.</li></ol>"),
         ("more", "3. Other festivals",
          "<p>The same tool has frames for Pongal and Sankranti, Holi, Eid, Onam, Ganesh Chaturthi, Christmas, New Year, birthdays and more. For a fresh good-morning image every day, try <a href=\"/daily-status-images\">Daily Status</a>.</p>"),
         ("cutout", "4. Want only the person?",
          "<p>Use <a href=\"/background-remover\">Background Remover</a> first to cut yourself out of a busy photo, then add the cut-out to the frame.</p>"),
     ], "related": ["remove-photo-background-profile-picture", "long-video-whatsapp-status"]},

    {"slug": "remove-photo-background-profile-picture", "cat": "photos", "icon": "photos", "read": 3, "tool": ("Background Remover", "/background-remover"),
     "title": "Remove a photo background for a profile picture",
     "desc": "Cut yourself out of any photo and put a clean white, coloured or blurred background behind you. The AI runs on your phone.",
     "lead": "A clean background makes a profile picture look professional. You can remove the background of any photo in a few seconds, without uploading it anywhere.",
     "sections": [
         ("choose", "1. Choose the right photo",
          "<p>The AI looks for a person. It works best when your face and shoulders are clear, in focus and not covered by other people or objects.</p>"),
         ("remove", "2. Remove the background",
          "<ol><li>Open <a href=\"/background-remover\">Background Remover</a> and tap <b>Choose photo</b>.</li>"
          "<li>The first time, your browser downloads the AI once. After that it is faster.</li>"
          "<li>Pick a background: transparent, white, a colour of your choice or a blurred version of the original.</li><li>Download the picture.</li></ol>"),
         ("where", "3. Where to use it",
          "<ul><li><b>WhatsApp, LinkedIn and email:</b> white or a soft colour looks professional.</li>"
          "<li><b>Forms:</b> many need a white background. <a href=\"/passport-photo-maker\">Passport Photo</a> also sets the exact size.</li>"
          "<li><b>Stickers:</b> <a href=\"/whatsapp-sticker-maker\">Sticker Maker</a> turns the cut-out into a WhatsApp sticker.</li></ul>"),
         ("privacy", "4. Your photo stays private",
          "<p>The AI runs inside your browser, so your photo is never uploaded to WaveRise.</p>"),
     ], "related": ["passport-photo-size-print", "diwali-greeting-own-photo"]},
]
BY_SLUG = {g["slug"]: g for g in GUIDES}
HOME_PICKS = ["resize-photo-20-50-kb-exam-forms", "clean-signature-online-application", "long-video-whatsapp-status"]


def icon(name, size=26):
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICONS.get(name, ICONS["photos"])}</svg>')


AD = '<div class="adanchor" data-ad="guide"></div>'


def article_html(g):
    toc = "".join(f'<li><a href="#{esc(i)}">{esc(t)}</a></li>' for i, t, _ in g["sections"])
    body = []
    for n, (i, t, h) in enumerate(g["sections"]):
        body.append(f'<h2 id="{esc(i)}">{esc(t)}</h2>{h}')
        if n == 0:
            body.append(AD)
        if n == 1:
            body.append(f'<div class="gcta"><span>Ready to try it? It takes about a minute.</span><a class="btn go" href="{esc(g["tool"][1])}">Open {esc(g["tool"][0])}</a></div>')
    rel = "".join(f'<a class="grel" href="/guides/{esc(s)}">{esc(BY_SLUG[s]["title"])}</a>' for s in g["related"] if s in BY_SLUG)
    return (f'<article class="page gpage" data-page="guide">'
            f'<nav class="crumb" aria-label="Breadcrumb"><a href="/guides">Guides</a> › <a href="/guides?topic={esc(g["cat"])}">{esc(CAT_NAME[g["cat"]])}</a></nav>'
            f'<h1>{esc(g["title"])}</h1>'
            f'<div class="byline"><span class="bav" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M3 14c3-6 6-6 9 0s6 6 9 0"/></svg></span>'
            f'<span><b>By {esc(AUTHOR)}</b><time datetime="{UPDATED}">Updated {UPDATED_TXT}</time> · {g["read"]} min read</span></div>'
            f'<p class="lead">{esc(g["lead"])}</p>'
            f'<nav class="toc" aria-label="On this page"><b>On this page</b><ol>{toc}</ol></nav>'
            + "".join(body) +
            f'<section class="grels" aria-labelledby="rel-h"><h2 id="rel-h">Related guides</h2>{rel}</section></article>')


def card_html(g):
    return (f'<a class="gcard" data-cat="{esc(g["cat"])}" href="/guides/{esc(g["slug"])}"><span class="gimg">{icon(g["icon"], 34)}</span>'
            f'<span class="gtxt"><span class="gcat">{esc(CAT_NAME[g["cat"]])}</span><b>{esc(g["title"])}</b><span class="gmeta">{g["read"]} min read</span></span></a>')


def index_html():
    chips = '<button type="button" class="gf" data-cat="" aria-pressed="true">All</button>' + "".join(
        f'<button type="button" class="gf" data-cat="{c}" aria-pressed="false">{esc(n)}</button>' for c, n in CATS)
    cards = [card_html(g) for g in GUIDES]
    grid = "".join(cards[:2]) + '<div class="adanchor gwide" data-ad="guides"></div>' + "".join(cards[2:])
    return (f'<article class="page gpage gindex" data-page="guide"><h1>Guides</h1>'
            f'<p class="lead">Step-by-step help for forms, photos and WhatsApp, written by the WaveRise team.</p>'
            f'<div class="gfilt" role="group" aria-label="Topics">{chips}</div><div class="ggrid">{grid}</div></article>')


def home_cards_html():
    return "".join(f'<a class="hguide" href="/guides/{esc(s)}"><span class="gimg">{icon(BY_SLUG[s]["icon"])}</span>'
                   f'<span><b>{esc(BY_SLUG[s]["title"])}</b><small>{esc(CAT_NAME[BY_SLUG[s]["cat"]])} · {BY_SLUG[s]["read"]} min read</small></span></a>'
                   for s in HOME_PICKS)


def popular_html():
    return "".join(f'<a href="/guides/{esc(s)}">{esc(BY_SLUG[s]["title"])}</a>' for s in
                   ["resize-photo-20-50-kb-exam-forms", "clean-signature-online-application", "passport-photo-size-print"])


def article_ld(g, base):
    url = f"{base}/guides/{g['slug']}"
    return {"@context": "https://schema.org", "@graph": [
        {"@type": "Article", "headline": g["title"], "description": g["desc"], "datePublished": UPDATED, "dateModified": UPDATED,
         "author": {"@type": "Organization", "name": AUTHOR, "url": base + "/about"},
         "publisher": {"@type": "Organization", "name": "WaveRise", "logo": {"@type": "ImageObject", "url": base + "/icon-512.png"}},
         "mainEntityOfPage": url, "inLanguage": "en"},
        {"@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Guides", "item": base + "/guides"},
            {"@type": "ListItem", "position": 2, "name": g["title"], "item": url}]}]}
