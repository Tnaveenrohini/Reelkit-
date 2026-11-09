"""WaveRise's own assistant brain. No API key, no outside AI, no limits.

It understands English, Telugu, Hindi and Tamil, in their own scripts or typed in English letters
("photo size ela taggali"), and answers in the same style. Knowledge comes from the site itself:
the help answers (HELP_KB), each tool's steps and FAQ (wr-guide) and the tool list (wr-seo).
"""
import difflib, json, random, re, time, unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
_TPL = Path(__file__).with_name("templates") / "index.html"


# ------------------------------------------------------------------ knowledge from the site
def _load():
    s = _TPL.read_text(encoding="utf-8")
    kb = json.loads(re.search(r"^const HELP_KB=(\[.*?\]);", s, re.M).group(1))
    guide = json.loads(re.search(r'<script type="application/json" id="wr-guide">(.*?)</script>', s, re.S).group(1))
    tw = re.search(r"TW=(\[\[.*?\]\]);", s)
    pairs = json.loads(tw.group(1).replace("'", '"')) if tw else []
    return kb, guide, pairs


KB, GUIDE, TOOL_WORDS = _load()
KB_BY = {e["id"]: e for e in KB}

def _seo():
    import wr_seo
    return wr_seo.SEO, wr_seo.path_of


def tool_name(k):
    seo, _ = _seo()
    return (seo.get(k) or {}).get("h") or k


def tool_link(k):
    seo, path_of = _seo()
    return f"[{tool_name(k)}]({path_of(k)})" if k in seo else tool_name(k)


# words people type in English letters, per language
ROMAN = {
    "tel": set("""ela elaa enti emi emiti cheyali cheyyali cheyadam cheyaali cheyandi cheyyandi chey kavali kaavali ledu ledhu
        undi undhi unnai unnay naku naaku nenu meeru mee ki lo ga gaa entha enduku endhuku ekkada taggali tagginchali tagginchu
        pettali raavatledu ravatledu raledu avvatledu avvadam pani chesi cheppu cheppandi bagundi baagundi sare andi anna akka
        ippudu inka kuda kooda adhi idhi adi idi vachindi vastundi vasthundi chala chaala konchem emaina evaru epudu eppudu
        cheyyatledu cheyatledu ayyindi avutundi avuthundi kani kaani mari leda nijamga peru telugu baaga thanks kada cheyacha cheyocha avutunda vastunda pothunda unda kavala ledha unnaya unnayi vachaya ayyaya cheppu chudu""".split()),
    "hil": set("""kaise kese kya kyaa hai hain nahi nahin nhi karna karo kar chahiye chaiye mera meri mujhe aap kyu kyun kahan kaha
        kitna kitni hoga raha rahi bhai yaar theek thik accha acha kaam bata batao batayein dijiye kijiye hota hoti gaya
        hindi shukriya dhanyavad wala wali""".split()),
    "tal": set("""eppadi epdi enna illa illai venum vendum panna pannunga pannanum seyya irukku iruku enakku ungal unga sollunga
        aagala aagudhu romba nalla enga yen yenna sari aamaa tamil nandri varala aagum mudiyala""".split()),
}
ROMAN_BODY = {"tel": "te", "hil": "hi", "tal": "ta"}


def norm(t):
    t = unicodedata.normalize("NFC", str(t or "").lower())
    t = "".join(c if unicodedata.category(c)[0] in "LNM" else " " for c in t)
    return " " + " ".join(t.split()) + " "


def words_of(t):
    return [w for w in norm(t).split() if w]


def style_of(q, lang="en"):
    """'te','hi','ta' (own script), 'tel','hil','tal' (typed in English letters) or 'en'."""
    if re.search("[ఀ-౿]", q): return "te"
    if re.search("[ऀ-ॿ]", q): return "hi"
    if re.search("[஀-௿]", q): return "ta"
    ws = set(words_of(q))
    sc = {k: len(ws & v) for k, v in ROMAN.items()}
    best = max(sc, key=sc.get)
    if sc[best] >= 2 or (sc[best] == 1 and len(ws) <= 4 and best != "hil") or (sc[best] == 1 and best == "hil" and ws & {"kaise", "kya", "nahi", "chahiye", "kahan"}):
        return best
    if lang in ("te", "hi", "ta") and len(ws) <= 2:
        return lang
    return "en"


def body_lang(st):
    return st if st in ("en", "te", "hi", "ta") else "en"


def has(q, k):
    k = k.lower()
    if re.fullmatch(r"[\x00-\x7f]+", k):
        return (" " + k + " ") in q or (len(k) >= 5 and k in q)
    return k in q


# ------------------------------------------------------------------ extra understanding
EXTRA_K = {
    "notwork": "raavatledu|ravatledu|raledu|avvatledu|avvadam ledu|pani cheyatledu|pani cheyyatledu|work avvatledu|work kavatledu|download avvatledu|"
               "error vastundi|error vasthundi|nahi ho raha|nahi chal raha|kaam nahi kar raha|nahi hua|aagala|varala|velai seyyala|work aagala|"
               "not opening|loading avutundi|loading lo undi|aagipoyindi|stuck",
    "kb": "size taggali|size tagginchali|size thagginchali|kb ki|size kam|size kam karna|size korai|photo size|photo chinnaga|"
          "photo kb|exam photo|form photo|photo upload avvatledu|too big|file size|reduce photo|compress photo|resize photo|resize|"
          "సైజ్ తగ్గ|సైజు తగ్గ|ఫోటో సైజ్|సైజ్|साइज़|साइज|साइज कम|फोटो छोटी|फ़ोटो छोटी|அளவு குறை|சைஸ் குறை|கேபி",
    "where": "ekkada save|ekkada untundi|ekkada undi|kahan save|kaha gaya|kahan gaya|enga save|enga irukku|gallery lo ledu|gallery mein nahi",
    "free": "free aa|free ah|free ena|paisa|paise|dabbulu|money|charge|free hai|cost entha|rate entha|subscription",
    "safe": "safe aa|safe ah|safe hai|password adugutunda|data safe",
    "how": "reel ela|video ela|download ela|save ela|kaise download|reel kaise|video kaise|eppadi download|eppadi save|download cheyali|"
           "download cheyyali|save cheyali|download karna hai|reel download|video download|insta video|instagram reel",
    "audio": "song kavali|audio kavali|mp3 kavali|gaana|paata|pata kavali|patalu|paatalu|music kavali|sound kavali|audio chahiye",
    "ringtone": "ringtone kavali|ringtone ela|ringtone banana|ringtone chey|ringtone cheyali",
    "install": "app undi|app unda|app hai|app download|install ela|play store|apk",
    "language": "telugu lo|hindi me|hindi mein|tamil la|tamil le|language marchali",
    "contact": "meeku ela contact|number ivvandi|complaint|whatsapp number|manishi tho|team tho matladali|talk to human|real person",
    "thanks": "thanks andi|dhanyavadalu|dhanyavaadalu|shukriya|nandri|thank u|tq|thx|tnx|thanku",
    "hello": "namaskaram|namaskar|hai|hii|hiii|hlo|helo|hey|vanakkam|namaste|hello andi|hi anna",
    "quality": "hd kavali|full hd|1080|quality ledu|blur ga undi|blurry|clear ga ledu",
    "stories": "story download|story save|highlights|dp download|profile pic",
    "passport": "passport size|passport photo|visa photo|sign size",
}
for _id, _w in EXTRA_K.items():
    if _id in KB_BY:
        KB_BY[_id]["k"] = list(dict.fromkeys(KB_BY[_id]["k"] + [w.strip() for w in _w.split("|") if w.strip()]))
PHRASES = {}

TOOL_EXTRA = [("సైజ్", "reduce"), ("సైజు", "reduce"), ("साइज", "reduce"), ("साइज़", "reduce"), ("அளவு", "reduce"), ("ఎడిట్", "editor"), ("एडिट", "editor"),
              ("రీల్", "video"), ("रील", "video"), ("ரீல்", "video"), ("పాస్‌పోర్ట్", "passport"), ("पासपोर्ट", "passport"), ("రింగ్‌టోన్", "ringtone"), ("रिंगटोन", "ringtone"),
              ("photo size", "reduce"), ("size taggali", "reduce"), ("kb", "reduce"), ("resize", "reduce"), ("exam photo", "reduce"),
              ("passport", "passport"), ("visa", "passport"), ("sign", "signature"), ("signature", "signature"),
              ("compress pdf", "pdfkb"), ("pdf size", "pdfkb"), ("pdf kb", "pdfkb"), ("reduce pdf", "pdfkb"), ("merge pdf", "mergepdf"), ("combine pdf", "mergepdf"), ("join pdf", "mergepdf"), ("pdf", "topdf"), ("bg", "bgremove"), ("background", "bgremove"), ("edit", "editor"), ("editor", "editor"),
              ("filter", "editor"), ("crop", "editor"), ("brightness", "editor"), ("song", "mp3"), ("audio", "mp3"),
              ("mp3", "mp3"), ("paata", "mp3"), ("ringtone", "ringtone"), ("trim", "clip"), ("cut", "clip"), ("gif", "gif"),
              ("sticker", "sticker"), ("status", "status"), ("whatsapp status", "status"), ("compress", "shrink"),
              ("reel", "video"), ("reels", "video"), ("insta", "video"), ("instagram", "video"), ("facebook", "facebook"),
              ("fb", "facebook"), ("twitter", "x"), ("threads", "threads"), ("caption", "caption"), ("hashtag", "caption"),
              ("festival", "festival"), ("diwali", "festival"), ("sankranti", "festival"), ("good morning", "daily"),
              ("wallpaper", "wallpapers"), ("wallpapers", "wallpapers"), ("carousel", "carousel"), ("name and date", "namedate")]

# conversation entries the help list doesn't have
NEW = [
    {"id": "editor", "go": ["editor"], "k": ["photo edit", "edit photo", "editor", "edit cheyali", "edit karna", "snapseed", "filter", "brightness", "lightroom", "photo bright"],
     "a": {"en": "Try the free [Photo Editor](/photo-editor): pick a photo, choose a look, then adjust brightness, contrast, warmth, crop or rotate. It all happens on your phone, so your photo isn't uploaded.",
           "te": "ఉచిత [Photo Editor](/photo-editor) వాడండి: ఫోటో ఎంచుకోండి, ఒక look ఎంచుకుని brightness, contrast, warmth, crop, rotate మార్చండి. అంతా మీ ఫోన్‌లోనే జరుగుతుంది.",
           "hi": "मुफ़्त [Photo Editor](/photo-editor) आज़माइए: फ़ोटो चुनें, कोई look चुनें, फिर brightness, contrast, warmth, crop या rotate बदलें। सब आपके फ़ोन पर ही होता है।",
           "ta": "இலவச [Photo Editor](/photo-editor) பயன்படுத்துங்கள்: புகைப்படத்தைத் தேர்ந்தெடுத்து look, brightness, contrast, crop, rotate மாற்றுங்கள். எல்லாம் உங்கள் போனிலேயே நடக்கும்.",
           "tel": "Mana free [Photo Editor](/photo-editor) try cheyandi andi: photo select chesi, oka look pettandi, tarvata brightness, contrast, warmth, crop, rotate anni marchukovachu. Photo mee phone lone untundi, upload avvadu.",
           "hil": "Hamara free [Photo Editor](/photo-editor) try kijiye: photo chuniye, look lagaiye, phir brightness, contrast, crop, rotate sab badal sakte ho. Photo aapke phone par hi rehti hai.",
           "tal": "Engal free [Photo Editor](/photo-editor) try pannunga: photo select panni, look, brightness, contrast, crop, rotate ellam maathalaam. Photo unga phone-la mattum dhaan irukkum."}},
    {"id": "community", "go": ["ringtones", "wallpapers"], "k": ["ringtones", "wallpapers", "wallpaper", "new ringtone", "latest ringtone", "bgm", "love ringtone", "devotional", "zedge", "mobile wallpaper"],
     "a": {"en": "We have a free library: [Ringtones](/ringtones) and [Wallpapers](/wallpapers), shared by the WaveRise community. Play or preview, then tap **Download**. No sign-in needed to download.",
           "te": "మన దగ్గర ఉచిత [Ringtones](/ringtones), [Wallpapers](/wallpapers) ఉన్నాయి. విని లేదా చూసి **Download** నొక్కండి. డౌన్‌లోడ్‌కి sign in అవసరం లేదు.",
           "hi": "हमारे पास मुफ़्त [Ringtones](/ringtones) और [Wallpapers](/wallpapers) हैं। सुनिए या देखिए, फिर **Download** दबाइए। डाउनलोड के लिए sign in ज़रूरी नहीं।",
           "ta": "இலவச [Ringtones](/ringtones), [Wallpapers](/wallpapers) உள்ளன. கேட்டு அல்லது பார்த்து **Download** அழுத்துங்கள். பதிவிறக்க sign in தேவையில்லை.",
           "tel": "Mana daggara free [Ringtones](/ringtones) inka [Wallpapers](/wallpapers) unnai andi. Play chesi vini, nachithe **Download** nokkandi. Download ki sign in avasaram ledu.",
           "hil": "Hamare paas free [Ringtones](/ringtones) aur [Wallpapers](/wallpapers) hain. Sun ke dekho, pasand aaye to **Download** dabao. Sign in ki zaroorat nahi.",
           "tal": "Free [Ringtones](/ringtones), [Wallpapers](/wallpapers) irukku. Play panni kettu, pidichaa **Download** pannunga. Sign in thevai illa."}},
    {"id": "upload", "go": ["ringtones"], "k": ["upload", "upload cheyali", "upload ela", "share my ringtone", "my ringtone", "na ringtone", "naa ringtone", "add ringtone", "post ringtone", "upload karna"],
     "a": {"en": "To share your own ringtone or wallpaper: open [Ringtones](/ringtones), tap **Sign in** (email + password, or Google), then **Upload**. Ringtones can be up to 60 seconds; wallpapers are resized for phones.",
           "te": "మీ ringtone/wallpaper షేర్ చేయడానికి: [Ringtones](/ringtones) తెరిచి **Sign in** (email + password లేదా Google) చేసి **Upload** నొక్కండి. Ringtone 60 సెకన్ల వరకు ఉండొచ్చు.",
           "hi": "अपनी ringtone या wallpaper शेयर करने के लिए: [Ringtones](/ringtones) खोलें, **Sign in** (email + password या Google) करें, फिर **Upload** दबाएँ। Ringtone 60 सेकंड तक हो सकती है।",
           "ta": "உங்கள் ringtone/wallpaper பகிர: [Ringtones](/ringtones) திறந்து **Sign in** செய்து **Upload** அழுத்துங்கள். Ringtone 60 வினாடி வரை.",
           "tel": "Mee own ringtone share cheyalante: [Ringtones](/ringtones) open chesi **Sign in** cheyandi (email + password leda Google), tarvata **Upload** nokkandi. Ringtone 60 seconds varaku undochu andi.",
           "hil": "Apni ringtone share karni hai to: [Ringtones](/ringtones) kholo, **Sign in** karo (email + password ya Google), phir **Upload** dabao. Ringtone 60 second tak ho sakti hai.",
           "tal": "Unga ringtone share panna: [Ringtones](/ringtones) open panni **Sign in** pannunga, apram **Upload**. Ringtone 60 second varaikum."}},
    {"id": "signin", "go": [], "k": ["login enduku", "login kavala", "login avasarama", "login kyu", "login", "log in", "sign in", "signin", "sign up", "signup", "account", "register", "password marchipoya", "forgot password", "password"],
     "a": {"en": "You don't need an account for any tool. Signing in is only for uploading, liking and following on [Ringtones](/ringtones): tap **Sign in** and use your email + password or Google. We never ask for your Instagram password.",
           "te": "ఏ టూల్‌కి account అవసరం లేదు. [Ringtones](/ringtones) లో upload, like, follow చేయడానికి మాత్రమే **Sign in** (email + password లేదా Google). మేము ఎప్పుడూ Instagram password అడగము.",
           "hi": "किसी टूल के लिए account नहीं चाहिए। [Ringtones](/ringtones) पर upload, like और follow के लिए ही **Sign in** (email + password या Google) है। हम कभी Instagram password नहीं माँगते।",
           "ta": "எந்த கருவிக்கும் account தேவையில்லை. [Ringtones](/ringtones) இல் upload, like, follow செய்ய மட்டும் **Sign in**. Instagram password ஒருபோதும் கேட்க மாட்டோம்.",
           "tel": "Tools ki account avasaram ledu andi. [Ringtones](/ringtones) lo upload, like, follow cheyadaniki matrame **Sign in** (email + password leda Google). Instagram password memu eppudu adagamu.",
           "hil": "Tools ke liye account nahi chahiye. Sirf [Ringtones](/ringtones) par upload, like, follow ke liye **Sign in** hai (email + password ya Google). Instagram password hum kabhi nahi maangte.",
           "tal": "Tools-ku account thevai illa. [Ringtones](/ringtones)-la upload, like, follow panna mattum **Sign in**. Instagram password naanga kekka maattom."}},
    {"id": "whoami", "go": [], "gen": True, "k": ["who are you", "are you bot", "are you a bot", "bot aa", "bot ah", "robot", "human aa", "nuvvu evaru", "meeru evaru", "tum kaun", "aap kaun", "nee yaar", "neenga yaar", "your name", "nee peru", "mee peru", "naam kya", "ai aa"],
     "a": {"en": "I'm WaveRise's own assistant: an automatic helper, not a person. I know every tool on the site and I'm here day and night. If you'd like a real person, write on the [Contact page](/contact) and the team will read it.",
           "te": "నేను WaveRise సొంత assistant ని, ఒక automatic helper, మనిషిని కాదు. సైట్‌లోని ప్రతి టూల్ నాకు తెలుసు. మనిషితో మాట్లాడాలంటే [Contact page](/contact) లో రాయండి.",
           "hi": "मैं WaveRise का अपना assistant हूँ, एक automatic helper, इंसान नहीं। साइट के हर टूल के बारे में जानता हूँ। किसी इंसान से बात करनी हो तो [Contact page](/contact) पर लिखिए।",
           "ta": "நான் WaveRise-இன் சொந்த assistant, ஒரு automatic helper, மனிதர் அல்ல. மனிதரிடம் பேச [Contact page](/contact) இல் எழுதுங்கள்.",
           "tel": "Nenu WaveRise own assistant ni andi, oka automatic helper, manishini kaadu 🙂 Site lo prathi tool naaku telusu, rathri pagalu ikkade untanu. Manishi tho matladali ante [Contact page](/contact) lo rayandi, team chustundi.",
           "hil": "Main WaveRise ka apna assistant hoon, ek automatic helper, insaan nahi 🙂 Har tool ke baare mein jaanta hoon. Insaan se baat karni ho to [Contact page](/contact) par likhiye.",
           "tal": "Naan WaveRise-oda own assistant, oru automatic helper, manushan illa 🙂 Ella tools-um theriyum. Manushan kitta pesanum na [Contact page](/contact)-la ezhudhunga."}},
    {"id": "howareyou", "go": [], "gen": True, "k": ["how are you", "how r u", "ela unnaru", "ela unnav", "ela unnavu", "bagunnara", "baagunnara", "kaise ho", "kaise hain", "kya haal", "eppadi irukeenga", "eppadi iruka", "sapteengala", "tinnara", "bhojanam ayyinda"],
     "a": {"en": "I'm doing great, thanks for asking! 😊 How can I help you today?",
           "te": "నేను బాగున్నాను, అడిగినందుకు థాంక్స్! 😊 ఈరోజు మీకు ఏం సహాయం చేయాలి?",
           "hi": "मैं बढ़िया हूँ, पूछने के लिए शुक्रिया! 😊 आज आपकी क्या मदद करूँ?",
           "ta": "நான் நன்றாக இருக்கிறேன், கேட்டதற்கு நன்றி! 😊 இன்று என்ன உதவி வேண்டும்?",
           "tel": "Nenu bagunnanu andi, adiginanduku thanks! 😊 Meeru ela unnaru? Eeroju meeku emi help kavali?",
           "hil": "Main badhiya hoon ji, poochne ke liye shukriya! 😊 Aap batao, aaj kya help chahiye?",
           "tal": "Naan nalla irukken, kettadhukku nandri! 😊 Neenga eppadi irukeenga? Enna help venum?"}},
    {"id": "bye", "go": [], "gen": True, "k": ["bye", "bye bye", "good night", "gn", "see you", "tata", "untanu", "vellostha", "vellosta", "chalta hoon", "phir milenge", "poitu varen", "ok bye"],
     "a": {"en": "Bye! Come back anytime. 👋", "te": "సరే, మళ్ళీ ఎప్పుడైనా రండి! 👋", "hi": "ठीक है, फिर कभी भी आइए! 👋", "ta": "சரி, எப்போது வேண்டுமானாலும் வாருங்கள்! 👋",
           "tel": "Sare andi, malli eppudaina randi! 👋", "hil": "Theek hai ji, phir aana! 👋", "tal": "Sari, eppo venumnaalum vaanga! 👋"}},
    {"id": "praise", "go": [], "gen": True, "k": ["super", "awesome", "nice", "great", "good site", "best site", "bagundi", "chala bagundi", "keka", "adurs", "mast", "badhiya", "zabardast", "semma", "nalla irukku", "love it", "amazing"],
     "a": {"en": "That made my day, thank you! 😊 If you like WaveRise, share it with a friend. Anything else I can do?",
           "te": "చాలా సంతోషం, థాంక్స్! 😊 నచ్చితే మీ ఫ్రెండ్స్‌తో షేర్ చేయండి.",
           "hi": "बहुत ख़ुशी हुई, धन्यवाद! 😊 पसंद आया हो तो दोस्तों के साथ शेयर कीजिए।",
           "ta": "ரொம்ப சந்தோஷம், நன்றி! 😊 பிடித்திருந்தால் நண்பர்களுடன் பகிருங்கள்.",
           "tel": "Chala santhosham andi, thanks! 😊 Nachithe mee friends ki kuda share cheyandi. Inka emaina kavala?",
           "hil": "Bahut khushi hui ji, shukriya! 😊 Pasand aaya to doston ko share karo. Aur kuch chahiye?",
           "tal": "Romba santhosham, nandri! 😊 Pidichirundha friends kitta share pannunga."}},
    {"id": "okay", "go": [], "gen": True, "k": ["ok", "okay", "ok andi", "sare", "sari", "theek hai", "hmm", "k", "fine", "achha", "acha", "avunu", "haan"],
     "a": {"en": "👍 Tell me whenever you need something.", "te": "👍 ఏమైనా కావాలంటే అడగండి.", "hi": "👍 कुछ भी चाहिए तो बताइए।", "ta": "👍 எதுவும் வேண்டுமென்றால் சொல்லுங்கள்.",
           "tel": "👍 Emaina kavalante adagandi andi.", "hil": "👍 Kuch bhi chahiye to batana.", "tal": "👍 Edhavadhu venumna sollunga."}},
    {"id": "chat", "go": ["chat"], "k": ["search", "find tool", "which tool", "edi tool", "ye tool", "all tools", "anni tools", "menu"],
     "a": {"en": "Use the search box at the top of any page (the 🔍 on phones): type what you need, like \"photo 50 KB\" or \"reel mp3\", and pick the tool. Or just tell me here!",
           "te": "ఏ పేజీ పైన అయినా search box (ఫోన్‌లో 🔍) లో మీకు కావాల్సింది టైప్ చేయండి, ఉదా. \"photo 50 KB\". లేదా ఇక్కడే నాకు చెప్పండి!",
           "hi": "किसी भी पेज के ऊपर search box (फ़ोन पर 🔍) में लिखिए, जैसे \"photo 50 KB\"। या मुझे यहीं बताइए!",
           "ta": "எந்தப் பக்கத்தின் மேலும் search box (போனில் 🔍) இல் தட்டச்சு செய்யுங்கள், உதா. \"photo 50 KB\". அல்லது இங்கே சொல்லுங்கள்!",
           "tel": "Prathi page paina search box undi (phone lo 🔍): \"photo 50 KB\" laaga type cheste tool vastundi. Leda ikkade naaku cheppandi andi!",
           "hil": "Har page ke upar search box hai (phone par 🔍): \"photo 50 KB\" jaisa likho, tool mil jayega. Ya mujhe yahin batao!",
           "tal": "Ovvoru page mela-um search box irukku (phone-la 🔍): \"photo 50 KB\" maadhiri type pannunga. Illa inga sollunga!"}},
]
ROMAN_ANS = {
    "thanks": {"tel": "Welcome andi! 😊 Inka emaina kavalante adagandi.", "hil": "Koi baat nahi ji! 😊 Aur kuch chahiye to batao.", "tal": "Paravaa illa! 😊 Vera edhavadhu venumna kelunga."},
    "free": {"tel": "Avunu andi, WaveRise poorthiga free! Login ledu, account ledu, watermark ledu, Instagram password eppudu adagamu.",
             "hil": "Haan ji, WaveRise bilkul free hai! Na login, na account, na watermark, aur Instagram password kabhi nahi maangte.",
             "tal": "Aamaa, WaveRise muzhusa free! Login illa, account illa, watermark illa."},
    "safe": {"tel": "Safe andi. Login ledu, password eppudu adagamu. Photo tools mee phone lone pani chestai, emi upload avvadu. Link ichhinappudu aa file teesukodaniki matrame vadatamu. [Privacy Policy](/privacy-policy) chudochu.",
             "hil": "Bilkul safe hai ji. Na login, na password. Photo tools aapke phone par hi chalte hain, kuch upload nahi hota. [Privacy Policy](/privacy-policy) dekh sakte ho.",
             "tal": "Safe dhaan. Login illa, password kekka maattom. Photo tools unga phone-la mattum velai seiyum. [Privacy Policy](/privacy-policy)."},
    "where": {"tel": "Download ayina file mee phone lo **Downloads** folder lo untundi andi (Android: Files → Downloads; iPhone: **Files** app). Chala phones lo konchem sepu tarvata gallery lo kuda kanipistundi. **Send to WhatsApp** tho direct share cheyochu.",
              "hil": "File aapke phone ke **Downloads** folder mein hoti hai ji (Android: Files → Downloads; iPhone: **Files** app). Thodi der mein gallery mein bhi dikh jaati hai.",
              "tal": "File unga phone-oda **Downloads** folder-la irukkum (Android: Files → Downloads; iPhone: **Files** app). Konja nerathula gallery-laum varum."},
    "notwork": {"tel": "Ayyo, sorry andi. Sadharanam ga ivi kaaranalu:\n- Post **private leda delete** ayyindi. Public posts matrame avutai.\n- Link sariga copy avvaledu. **Share → Copy link** tho malli paste cheyandi.\n- Instagram/Facebook ippudu limit pedutondi. Konchem sepu aagi malli try cheyandi.\nInka avvakapothe aa link ni [Contact page](/contact) lo pampandi.",
                "hil": "Arre, sorry ji. Aam wajah ye hoti hain:\n- Post **private ya delete** hai. Sirf public posts chalti hain.\n- Link sahi copy nahi hua. **Share → Copy link** se dobara paste karo.\n- Instagram/Facebook abhi limit laga raha hai. Thodi der baad try karo.\nPhir bhi na ho to link [Contact page](/contact) par bhejo.",
                "tal": "Aiyo, sorry nga. Pothuva kaaranam:\n- Post **private illa delete** aagirukku. Public posts mattum dhaan.\n- Link sariya copy aagala. **Share → Copy link** panni marubadiyum paste pannunga.\n- Instagram limit pannudhu. Konja neram kazhichu try pannunga.\nInnum aagala na [Contact page](/contact)-la link anuppunga."},
    "private": {"tel": "WaveRise **public** posts ni matrame save chestundi andi. Private accounts posts save cheyalemu, mee password kuda adagamu.",
                "hil": "WaveRise sirf **public** posts save karta hai ji. Private account ki posts save nahi ho sakti.",
                "tal": "WaveRise **public** posts mattum dhaan save pannum. Private account posts mudiyaadhu."},
    "stories": {"tel": "Stories, Highlights, profile pictures ni privacy kosam kavalane support cheyamu andi. Public Reels, videos, photos, carousels save cheyochu.",
                "hil": "Stories, Highlights aur profile pictures privacy ke liye jaan-boojh kar support nahi hain ji. Public Reels, videos, photos save kar sakte ho.",
                "tal": "Stories, Highlights, profile pictures privacy-kaga support illa. Public Reels, videos, photos save pannalaam."},
    "soon": {"tel": "YouTube **coming soon** andi, inka ready avvaledu. Threads ippude pani chestundi: [Threads downloader](/threads-video-downloader).",
             "hil": "YouTube **coming soon** hai ji, abhi available nahi. Threads abhi chalta hai: [Threads downloader](/threads-video-downloader).",
             "tal": "YouTube **coming soon**, innum varala. Threads ippo velai seiyum: [Threads downloader](/threads-video-downloader)."},
    "kb": {"tel": "Sare andi! [Photo MB to KB](/photo-mb-to-kb) open cheyandi: photo select chesi, form adigina size (udaharanaki Under 50 KB) pettandi leda mee number type cheyandi, tarvata download. Antha mee phone lone, emi upload avvadu.",
           "hil": "Haan ji! [Photo MB to KB](/photo-mb-to-kb) kholo: photo chuno, form jo size maange (jaise Under 50 KB) wo chuno ya khud likho, phir download. Sab aapke phone par hi hota hai.",
           "tal": "Sari! [Photo MB to KB](/photo-mb-to-kb) open pannunga: photo select panni, form ketkura size (Under 50 KB maadhiri) choose pannunga, apram download. Ellam unga phone-la dhaan."},
    "how": {"tel": "Simple andi:\n1. Instagram lo Reel open chesi **Share → Copy link** nokkandi.\n2. WaveRise ki vachi kinda box lo paste cheyandi.\n3. Gold send button nokki, tarvata **Download**.",
            "hil": "Bahut aasaan hai ji:\n1. Instagram mein Reel kholo, **Share → Copy link** dabao.\n2. WaveRise par aakar neeche box mein paste karo.\n3. Gold send button dabao, phir **Download**.",
            "tal": "Romba easy:\n1. Instagram-la Reel open panni **Share → Copy link**.\n2. WaveRise-la keezha box-la paste pannunga.\n3. Gold send button, apram **Download**."},
    "audio": {"tel": "[Video to MP3](/video-to-mp3) vadandi andi: Reel link paste cheste sound MP3 ga vastundi. Sound takkuva ga unte **Louder sound** on cheyandi.",
              "hil": "[Video to MP3](/video-to-mp3) use karo ji: Reel ka link paste karo, sound MP3 mein mil jayegi. Awaaz kam ho to **Louder sound** on karo.",
              "tal": "[Video to MP3](/video-to-mp3) use pannunga: Reel link paste panna sound MP3-aa kidaikkum."},
    "ringtone": {"tel": "Rendu options unnai andi: [Reel to Ringtone](/reel-to-ringtone) tho Reel lo 30 seconds part ni ringtone ga cheyochu, leda mana [Ringtones](/ringtones) library lo ready ga unna vaati nunchi download cheyochu.",
                 "hil": "Do tareeke hain ji: [Reel to Ringtone](/reel-to-ringtone) se Reel ka 30 second ka hissa ringtone banao, ya hamari [Ringtones](/ringtones) library se ready ringtone download karo.",
                 "tal": "Rendu vazhi: [Reel to Ringtone](/reel-to-ringtone)-la Reel-oda 30 second ringtone-aa pannalaam, illa [Ringtones](/ringtones) library-la irundhu download pannalaam."},
    "install": {"tel": "Chrome lo **⋮** menu → **Install app** nokkandi andi. Tarvata Instagram lo Reel meeda **Share** nokki WaveRise select cheste direct ga vastundi.",
                "hil": "Chrome mein **⋮** menu → **Install app** dabao ji. Phir Instagram mein Reel par **Share** karke WaveRise chuno.",
                "tal": "Chrome-la **⋮** menu → **Install app**. Apram Instagram-la Reel **Share** panni WaveRise select pannunga."},
    "language": {"tel": "Paina **EN** button nokki English, हिन्दी, தமிழ், తెలుగు lo edaina select cheyochu andi.",
                 "hil": "Upar **EN** button dabao, English, हिन्दी, தமிழ், తెలుగు mein se chuno ji.", "tal": "Mela **EN** button-la language maathalaam."},
    "contact": {"tel": "[Contact page](/contact) lo message pettandi andi, adi direct ga WaveRise team ki vastundi. Reply kavalante mee email leda phone number kuda rayandi.",
                "hil": "[Contact page](/contact) par message bhejo ji, wo seedha WaveRise team ko jaata hai. Reply chahiye to email ya phone number likho.",
                "tal": "[Contact page](/contact)-la message anuppunga, adhu nera team-ku pogum."},
    "quality": {"tel": "Link paste chesaka site ichhe anni qualities kanipistai andi. Video ni **Enhance to HD 1080p** tho clear ga cheyochu (1 minute varaku). Original lo leni detail matram raadu.",
                "hil": "Link paste karne ke baad saari qualities dikhti hain ji. **Enhance to HD 1080p** se video saaf ho jata hai (1 minute tak).",
                "tal": "Link paste panna ella quality-um theriyum. **Enhance to HD 1080p** use pannalaam (1 minute varaikum)."},
    "passport": {"tel": "[Passport Photo](/passport-photo-maker) tho passport/visa size photo, [Signature Maker](/signature-resizer) tho signature ni exact size & KB lo cheyochu andi. Rendu mee phone lone.",
                 "hil": "[Passport Photo](/passport-photo-maker) se passport/visa size photo, aur [Signature Maker](/signature-resizer) se signature exact size mein banao ji.",
                 "tal": "[Passport Photo](/passport-photo-maker)-la passport size photo, [Signature Maker](/signature-resizer)-la signature pannalaam."},
}
for _id, _a in ROMAN_ANS.items():
    if _id in KB_BY:
        KB_BY[_id]["a"].update(_a)

NEW_BY = {e["id"]: e for e in NEW}
GENERAL = {"hello", "thanks", "whoami", "howareyou", "bye", "praise", "okay"}

ABUSE = re.compile(r"\b(f+u+c+k+|shit|bastard|idiot|stupid|useless|waste|worst|nonsense|bakwas|bekaar|bekar|chetha|chettha|dengey|dengu|lanja|kukka|pichi|madarchod|bhosdi|chutiya|gandu|loosu|naaye|poda)\b", re.I)
SAD = re.compile(r"\b(worst|waste|useless|bakwas|bekar|chetha|frustrat|angry|kopam|gussa|not happy|disappointed|again and again|malli malli|baar baar)\b", re.I)
FOLLOW = re.compile(r"^\s*(how|how\?|then|next|and then|steps?|ela|ela\?|elaa|ela cheyali|adi ela|idi ela|tarvata|taruvatha|next enti|inka|kaise|kaise\?|phir|uske baad|eppadi|apram|aprom|where|ekkada|kahan|enga|why|enduku|kyu|kyun|yen|more|inka cheppu|explain|details?)\s*\??\s*$", re.I)

OPEN = {
    "en": {"help": ["Sure!", "Of course!", "Happy to help.", "Got it."], "sorry": ["Ah, sorry about that.", "Oh no, let's sort it out.", "That's annoying, sorry!"],
           "steps": ["Here's how:", "Easy, just do this:", "Sure, follow these steps:"], "again": ["Let me explain it once more:", "No problem, here it is again:"]},
    "tel": {"help": ["Sare andi!", "Tappakunda andi!", "Ok andi, cheptanu.", "Sure andi."], "sorry": ["Ayyo, sorry andi.", "Arey, sorry andi, fix cheddam.", "Sorry andi, ila try cheyandi."],
            "steps": ["Ila cheyandi andi:", "Simple andi, ee steps follow avvandi:", "Cheptanu andi:"], "again": ["Sare, inkosari cheptanu andi:", "Parledu andi, malli cheptanu:"]},
    "hil": {"help": ["Haan ji!", "Bilkul!", "Zaroor, batata hoon."], "sorry": ["Arre, sorry ji.", "Oh, maaf kijiye, chaliye theek karte hain."],
            "steps": ["Aise kijiye:", "Bas ye steps follow karo:"], "again": ["Koi baat nahi, phir se batata hoon:"]},
    "tal": {"help": ["Sari!", "Kandippa!", "Seri, solren."], "sorry": ["Sorry nga.", "Aiyo, sorry, sari pannalam."],
            "steps": ["Ippadi pannunga:", "Indha steps follow pannunga:"], "again": ["Paravaa illa, marubadiyum solren:"]},
    "te": {"help": ["సరే అండి!", "తప్పకుండా!"], "sorry": ["అయ్యో, క్షమించండి."], "steps": ["ఇలా చేయండి:"], "again": ["సరే, మళ్ళీ చెప్తాను:"]},
    "hi": {"help": ["ज़रूर!", "बिल्कुल!"], "sorry": ["माफ़ कीजिए।"], "steps": ["ऐसे कीजिए:"], "again": ["कोई बात नहीं, फिर से बताता हूँ:"]},
    "ta": {"help": ["சரி!", "கண்டிப்பாக!"], "sorry": ["மன்னிக்கவும்."], "steps": ["இப்படி செய்யுங்கள்:"], "again": ["சரி, மீண்டும் சொல்கிறேன்:"]},
}
CLOSE = {
    "en": ["Anything else?", "Let me know if it works!", "Tell me if you get stuck.", "", ""],
    "tel": ["Inka emaina doubt unte adagandi 🙂", "Ayyaka cheppandi andi!", "Ekkadaina aagipothe cheppandi.", "", ""],
    "hil": ["Aur kuch chahiye to batao 🙂", "Ho jaye to batana!", "", ""],
    "tal": ["Vera edhavadhu venumna kelunga 🙂", "Aanadhum sollunga!", "", ""],
    "te": ["ఇంకేమైనా కావాలంటే అడగండి 🙂", ""], "hi": ["और कुछ चाहिए तो बताइए 🙂", ""], "ta": ["வேறு ஏதாவது வேண்டுமா? 🙂", ""],
}
UNSURE = {
    "en": ["Hmm, I'm not sure I got that. Do you want to **make a photo smaller** for a form, **save a Reel**, or **edit a photo**? Tell me in a few words.",
           "Sorry, I didn't quite get that. What are you trying to do? For example: \"photo 50 KB\", \"save reel\" or \"make ringtone\"."],
    "tel": ["Hmm, naaku sariga artham kaaledu andi. Meeku **photo size taggincha**la, **reel save** cheyala, leka **photo edit** cheyala? Konchem cheppandi.",
            "Sorry andi, artham kaaledu. Meeru emi cheyali anukuntunnaru? Udaharanaki: \"photo 50 KB\", \"reel save\", \"ringtone kavali\"."],
    "hil": ["Hmm, samajh nahi aaya ji. Aapko **photo chhoti** karni hai, **reel save** karni hai, ya **photo edit**? Thoda batao.",
            "Sorry, samjha nahi. Kya karna chahte ho? Jaise: \"photo 50 KB\", \"reel save\", \"ringtone chahiye\"."],
    "tal": ["Hmm, puriyala. **Photo size kuraikanuma**, **reel save** pannanuma, illa **photo edit**-aa? Konjam sollunga."],
    "te": ["క్షమించండి, సరిగ్గా అర్థం కాలేదు. మీకు **ఫోటో సైజ్ తగ్గించాలా**, **రీల్ సేవ్ చేయాలా**, లేక **ఫోటో ఎడిట్**? కొంచెం చెప్పండి."],
    "hi": ["माफ़ कीजिए, समझ नहीं आया। आपको **फ़ोटो छोटी** करनी है, **रील सेव** करनी है या **फ़ोटो एडिट**? थोड़ा बताइए।"],
    "ta": ["மன்னிக்கவும், புரியவில்லை. **புகைப்பட அளவைக் குறைக்க**, **ரீல் சேமிக்க**, அல்லது **புகைப்படம் திருத்த**? கொஞ்சம் சொல்லுங்கள்."],
}
ABUSE_REPLY = {
    "en": "I'm sorry it's been frustrating. 🙏 Tell me what went wrong and I'll do my best to fix it with you.",
    "tel": "Sorry andi, ibbandi ayyindi ani artham avutondi 🙏 Emi jarigindo cheppandi, kalisi fix cheddam.",
    "hil": "Sorry ji, pareshani hui samajh sakta hoon 🙏 Batao kya hua, saath mein theek karte hain.",
    "tal": "Sorry nga, kashtama irundhuruku 🙏 Enna aachu nu sollunga, sari pannalam.",
    "te": "క్షమించండి, ఇబ్బంది అయ్యిందని అర్థమవుతోంది 🙏 ఏమైందో చెప్పండి, కలిసి సరిచేద్దాం.",
    "hi": "माफ़ कीजिए, परेशानी हुई 🙏 बताइए क्या हुआ, मिलकर ठीक करते हैं।",
    "ta": "மன்னிக்கவும், சிரமமாக இருந்திருக்கும் 🙏 என்ன ஆனது என்று சொல்லுங்கள்.",
}
STILL = {"en": "If it still doesn't work, send us the details on the [Contact page](/contact). A person from the team will look at it.",
         "tel": "Inka kuda avvakapothe [Contact page](/contact) lo details pettandi andi, team lo okaru chustaru.",
         "hil": "Phir bhi na ho to [Contact page](/contact) par details bhejo, team ka koi dekhega.",
         "tal": "Innum aagala na [Contact page](/contact)-la details anuppunga, team paarpanga.",
         "te": "ఇంకా పని చేయకపోతే [Contact page](/contact) లో వివరాలు పంపండి.", "hi": "फिर भी न हो तो [Contact page](/contact) पर बताइए।", "ta": "இன்னும் சரியாகவில்லை என்றால் [Contact page](/contact) இல் எழுதுங்கள்."}


def pick(lst):
    lst = [x for x in lst if x is not None]
    return random.choice(lst) if lst else ""


def strip_md(t):
    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", re.sub(r"\*\*([^*]+)\*\*", r"\1", t))


# ------------------------------------------------------------------ understanding
def detect_tools(qn):
    seo, _ = _seo()
    found = []
    for k, v in list(TOOL_WORDS) + TOOL_EXTRA:
        if has(qn, k) and v in seo and v not in found:
            found.append(v)
    return found


def score_entries(qn, n_words):
    best, bs = None, 0.0
    ws = qn.split()
    for e in list(KB) + NEW:
        sc = 0.0
        for k in e["k"]:
            if has(qn, k):
                sc += 3 if " " in k else 2 if len(k) >= 4 else 1
        for p in PHRASES.get(e["id"], []):
            if (" " + p + " ") in qn:
                sc += 3
        if sc == 0:                                         # forgive small typos ("downlod", "notworking")
            for k in e["k"]:
                if re.fullmatch(r"[a-z]{5,}", k) and difflib.get_close_matches(k, ws, 1, 0.84):
                    sc += 1.5
                    break
        gen = e["id"] in GENERAL or e.get("gen")
        if gen and sc > 0:
            sc = 1.5 if n_words > 5 else max(sc, 2)
        if e["id"] == "hello" and n_words > 6:
            sc = 0
        if sc > bs:
            best, bs = e, sc
    return best, bs


FAQ_STOP = set("a an the is it my i to of for in on and or can do does what how why where when will you your me with this that be are was not".split())


def faq_match(qn, tools):
    qs = set(qn.split()) - FAQ_STOP
    if len(qs) < 2:
        return None
    best, bs = None, 0
    for k in (tools or list(GUIDE)):
        for qa in (GUIDE.get(k) or {}).get("q", []):
            fs = set(norm(qa[0]).split()) - FAQ_STOP
            if not fs:
                continue
            ov = len(qs & fs)
            sc = ov / len(fs | qs)
            if ov >= 2 and sc > bs:
                best, bs = (k, qa), sc
    return best if bs >= 0.34 else None


def steps_for(k, qn, st):
    g = GUIDE.get(k) or {}
    steps = g.get("st") or []
    lines = []
    m = re.search(r"(\d{1,4})\s*kb", qn)
    for i, s in enumerate(steps[:5], 1):
        if m and k == "reduce" and "size" in s.lower():
            s += f" For you: pick **Under {m.group(1)} KB**, or type {m.group(1)} in Type my own."
        lines.append(f"{i}. {s}")
    head = pick(OPEN.get(st, OPEN["en"])["steps"]) + " " + ("Open " if body_lang(st) == "en" else "") + tool_link(k)
    if st == "tel":
        head = pick(OPEN["tel"]["steps"]) + " " + tool_link(k) + " open cheyandi."
    elif st == "hil":
        head = pick(OPEN["hil"]["steps"]) + " " + tool_link(k) + " kholiye."
    elif st == "tal":
        head = pick(OPEN["tal"]["steps"]) + " " + tool_link(k) + " open pannunga."
    elif st in ("te", "hi", "ta"):
        head = pick(OPEN[st]["steps"]) + " " + tool_link(k)
    else:
        head = head.rstrip() + "."
    if not lines:
        seo, _ = _seo()
        return head + "\n" + (seo.get(k, {}).get("a") or seo.get(k, {}).get("d") or "")[:300]
    return head + "\n" + "\n".join(lines)


def kb_answer(e, st):
    a = e["a"]
    if st in a:
        return a[st]
    if st in ("tel", "hil", "tal"):
        body = a.get("en", "")
        first = re.match(r"^([^.!?\n]{1,40}[!])\s*", body)          # drop a lone "Sorry about that!" style opener
        if first:
            body = body[first.end():]
        kind = "sorry" if e["id"] in ("notwork", "private", "stories") else "help"
        return pick(OPEN[st][kind]) + " " + body
    return a.get(body_lang(st)) or a.get("en", "")


def greet(st):
    h = datetime.now(IST).hour
    part = "Good morning" if 4 <= h < 12 else "Good afternoon" if h < 17 else "Good evening"
    return {"en": f"{part}! 👋 I'm here to help with WaveRise. What would you like to do: save a Reel, make a photo smaller for a form, or something else?",
            "tel": "Namaskaram andi! 👋 Nenu WaveRise assistant ni. Eeroju emi cheyali: reel save cheyala, form kosam photo size taggincha la, leka inkemaina?",
            "hil": "Namaste ji! 👋 Main WaveRise assistant hoon. Aaj kya karna hai: reel save, form ke liye photo chhoti, ya kuch aur?",
            "tal": "Vanakkam! 👋 Naan WaveRise assistant. Inniku enna pannanum: reel save, form-ku photo size kuraikanum, illa vera edhavadhu?",
            "te": "నమస్కారం అండి! 👋 ఈరోజు ఏం చేయాలి: రీల్ సేవ్, ఫారమ్ కోసం ఫోటో సైజ్ తగ్గించడం, లేక ఇంకేమైనా?",
            "hi": "नमस्ते! 👋 आज क्या करना है: रील सेव, फ़ॉर्म के लिए फ़ोटो छोटी, या कुछ और?",
            "ta": "வணக்கம்! 👋 இன்று என்ன செய்ய வேண்டும்: ரீல் சேமிப்பு, படிவத்துக்கு புகைப்பட அளவு குறைப்பு, அல்லது வேறு?"}[st]


def last_topic(hist):
    """The tool or help topic the visitor was last talking about."""
    for m in reversed(hist or []):
        if not isinstance(m, dict) or not m.get("t"):
            continue
        qn = norm(m["t"])
        if m.get("me"):
            tools = detect_tools(qn)
            if tools:
                return ("tool", tools[0])
            e, sc = score_entries(qn, len(qn.split()))
            if e and sc >= 2 and e["id"] not in GENERAL:
                return ("kb", e["id"])
    return None


# ------------------------------------------------------------------ visitor reply
def visitor(q, hist=None, lang="en"):
    """Returns (answer_markdown, tool_keys)."""
    q = re.sub(r"\s+", " ", str(q or "")).strip()[:500]
    if not q:
        return None, []
    st = style_of(q, lang)
    if st == "en" and lang in ("te", "hi", "ta") and not re.search(r"[a-z]{3,}", q.lower()):
        st = lang
    qn = norm(q)
    n = len(qn.split())
    prev_bot = next((m.get("t") for m in reversed(hist or []) if isinstance(m, dict) and not m.get("me") and m.get("t")), "")

    def finish(text, go, kind=None):
        text = text.strip()
        if prev_bot and strip_md(text)[:80] == strip_md(prev_bot)[:80]:
            text = pick(OPEN.get(st, OPEN["en"])["again"]) + "\n" + text + "\n\n" + STILL[st]
        elif kind != "gen":
            c = pick(CLOSE.get(st, CLOSE["en"]))
            if c and not text.endswith("?"):
                text += "\n\n" + c
        return text, go

    if ABUSE.search(q) and n <= 12 and not detect_tools(qn):
        return ABUSE_REPLY[st], []
    m = re.search(r"\b(?:my name is|naa peru|na peru|mera naam|en peyar)\s+([a-zA-Z]{2,20})", q, re.I)
    if m:
        nm = m.group(1).capitalize()
        return {"en": f"Nice to meet you, {nm}! 😊 How can I help?", "tel": f"Nice to meet you {nm} garu! 😊 Emi help kavali?",
                "hil": f"Aapse milkar accha laga, {nm} ji! 😊 Kya help chahiye?", "tal": f"Ungala sandhichadhu santhosham, {nm}! 😊 Enna help venum?",
                "te": f"మిమ్మల్ని కలవడం సంతోషం {nm} గారు! 😊", "hi": f"आपसे मिलकर ख़ुशी हुई, {nm} जी! 😊", "ta": f"உங்களைச் சந்தித்ததில் மகிழ்ச்சி, {nm}! 😊"}[st], []

    tools = detect_tools(qn)
    e, sc = score_entries(qn, n)
    sad = bool(SAD.search(q))
    if re.search(r"\b(youtube|yt|you tube|यूट्यूब|యూట్యూబ్|யூடியூப்)\b", qn) and "soon" in KB_BY:
        return finish(kb_answer(KB_BY["soon"], st), [], "gen")

    # greetings with nothing else
    if e and e["id"] == "hello" and not tools and n <= 5:
        return greet(st), []
    # a question about a specific tool's FAQ
    fq = faq_match(qn, tools) if tools or n >= 4 else None
    if e and sc >= 2 and not (e["id"] == "how" and tools and tools[0] != "video") and not (fq and e["id"] in GENERAL):
        go = list(dict.fromkeys((e.get("go") or []) + tools))
        text = kb_answer(e, st)
        if sad and e["id"] not in GENERAL:
            text = pick(OPEN.get(st, OPEN["en"])["sorry"]) + " " + text if st in ("en",) else text
        return finish(text, go, "gen" if e["id"] in GENERAL or e.get("gen") else None)
    if fq:
        k, qa = fq
        lead = {"tel": "Manchi prashna andi. ", "hil": "Accha sawaal hai. ", "tal": "Nalla kelvi. ", "en": pick(["Good question! ", "", "Sure: "])}.get(st, "")
        return finish(lead + qa[1] + f"\n\n{tool_link(k)}", [k])
    if tools:
        k = tools[0]
        e2 = next((x for x in KB if k in (x.get("go") or []) and x["id"] not in ("how",)), None)
        if e2 and body_lang(st) != "en" and st in ("te", "hi", "ta"):
            return finish(kb_answer(e2, st), tools)
        return finish(steps_for(k, qn, st), tools)
    if e and sc >= 1.5:
        return finish(kb_answer(e, st), e.get("go") or [], "gen" if e["id"] in GENERAL or e.get("gen") else None)
    if FOLLOW.match(q) or n <= 3:
        lt = last_topic(hist)
        if lt and lt[0] == "tool":
            return finish(steps_for(lt[1], qn, st), [lt[1]])
        if lt and lt[0] == "kb":
            ent = KB_BY.get(lt[1]) or NEW_BY.get(lt[1])
            return finish(kb_answer(ent, st), ent.get("go") or [])
    return pick(UNSURE.get(st, UNSURE["en"])), ["reduce", "video", "editor"]


# ------------------------------------------------------------------ owner assistant
def owner_style(q):
    st = style_of(q)
    return "tel" if st in ("tel", "te") else "en"


def _real(p):
    return p.get("type") in ("server", "browser", "check")


def explain_problem(row, st="en"):
    """Plain-words explanation of one noted problem."""
    msg, src, where, detail = row.get("msg", ""), row.get("source", ""), row.get("where", ""), row.get("detail", "")
    low = (msg + " " + detail).lower()
    T = lambda en, tel: tel if st == "tel" else en
    if re.search(r"private|deleted|not available|unavailable|log ?in|removed|no video|no media|isn.t public|age", low):
        return T(f"**Not a bug.** A visitor tried a post that is private, deleted or not public, so it can't be saved. It happened {row.get('count', 1)} time(s). Nothing to fix.",
                 f"**Idi bug kaadu andi.** Visitor private/delete ayina post try chesaru, andukane save avvaledu. {row.get('count', 1)} sarlu jarigindi. Fix cheyalsindi emi ledu.")
    if re.search(r"429|rate|too many|limit|try again in|temporarily|blocked|challenge", low):
        return T("**Instagram/Facebook is limiting our server right now.** Visitors see \"try again in a few minutes\". It usually clears by itself. If it keeps happening for hours, the server address may be blocked; let me know in this chat and I'll suggest options.",
                 "**Instagram/Facebook mana server ni konchem aaputundi (limit).** Visitors ki \"konchem sepu tarvata try cheyandi\" ani vastundi. Sadharanam ga daanikade clear avutundi. Gantala tarabadi untene problem.")
    if re.search(r"timeout|timed out|504|took too long|deadline", low):
        return T("**The server took too long.** Usually a very long or large video. If it is frequent, it may need a shorter limit for long videos.",
                 "**Server ki time saripoledu.** Ekkuva ga pedda/long video valla. Tarachu vaste long videos ki limit pettali.")
    if re.search(r"413|too large|too big|file size|max", low):
        return T("**The file was too big** for that tool. Visitors see the size limit message. Not a bug.",
                 "**File chala pedda ga undi** aa tool ki. Visitor ki size limit message vastundi. Bug kaadu.")
    if src == "browser":
        dev = detail.strip().splitlines()[-1] if detail.strip() else ""
        return T(f"**A real bug in the page** `{where}`: the browser hit `{msg}`. Visitors may see a button that does nothing or a part that doesn't load. It happened {row.get('count', 1)} time(s){' on ' + dev if dev else ''}. Open that page on the same kind of phone and try the main button; if it started after an update, an old cached page can also cause it (a refresh fixes that).",
                 f"**Page lo real bug andi** `{where}`: browser lo `{msg}` vachindi. Visitors ki button pani cheyakapovachu leda oka part load avvakapovachu. {row.get('count', 1)} sarlu{' (' + dev + ')' if dev else ''}. Aa page ni same type phone lo open chesi main button try cheyandi. Update tarvata start ayite, old cache page valla kuda avvachu (refresh tho pothundi).")
    if src == "server" or (row.get("status") or 0) >= 500:
        at = re.search(r"([\w.]+\.py:\d+[^←\n]*)", detail)
        return T(f"**A real bug on the server** at `{where}`: `{msg}`{' (in ' + at.group(1).strip() + ')' if at else ''}. Visitors got an error instead of a result. This needs a code fix; share this card with your developer (or with me in Claude) and it can be fixed quickly.",
                 f"**Server lo real bug andi** `{where}` daggara: `{msg}`{' (' + at.group(1).strip() + ')' if at else ''}. Visitors ki result badulu error vachindi. Code fix kavali; ee card ni developer ki (leda Claude lo naaku) chupinchandi.")
    if src == "check":
        return T(f"**The daily site check found a problem:** {msg}. {detail[:200]} Open the SEO Health page for details.",
                 f"**Daily site check lo problem kanipinchindi:** {msg}. {detail[:200]} Details kosam SEO Health page chudandi.")
    if src == "ai":
        return T("The optional Gemini helper couldn't answer, so the site's own assistant answered instead. Nothing for visitors to notice.",
                 "Optional Gemini helper answer ivvaledu, kaani mana own assistant answer icchindi. Visitors ki emi teliyadu.")
    return T(f"Visitors saw: \"{msg}\" at `{where}` ({row.get('count', 1)} time(s)). If it's a message about their link or file, it's not a bug.",
             f"Visitors ki \"{msg}\" ani vachindi `{where}` lo ({row.get('count', 1)} sarlu). Link/file gurinchi message ayite bug kaadu.")


def _ago_txt(s, st):
    return s


def owner(q, snap, hist=None):
    q = str(q or "").strip()
    st = owner_style(q)
    qn = norm(q)
    T = lambda en, tel: tel if st == "tel" else en
    probs = snap.get("problems_last_3_days") or []
    real = [p for p in probs if _real(p) and not p.get("marked_fixed")]
    vis = [p for p in probs if p.get("type") == "visitor" and not p.get("marked_fixed")]
    msgs = snap.get("contact_messages") or {}
    unread = msgs.get("unread", 0)
    dl = snap.get("downloads_today_and_yesterday_utc") or {}
    req = sum(v.get("requests", 0) for v in dl.values()); fail = sum(v.get("failure", 0) for v in dl.values())
    hc = snap.get("last_site_check") or {}
    rep = (snap.get("community") or {}).get("reported_uploads_waiting", 0)

    if not snap.get("database_connected"):
        return T("I can't see the site's records: the Upstash database isn't connected. Connect it in Vercel → Storage, and I'll be able to tell you everything.",
                 "Database connect ayyi ledu andi, anduke naaku site records kanipinchavu. Vercel → Storage lo Upstash connect cheyandi.")

    def bugs_part(limit=4):
        if not real:
            return T("✅ **No real bugs** in the last 3 days.", "✅ **Last 3 rojullo real bugs emi levu** andi.")
        lines = [T(f"⚠️ **{len(real)} real bug(s)** noticed:", f"⚠️ **{len(real)} real bug(s)** gamaninchanu:")]
        for p in sorted(real, key=lambda x: -x.get("times", 0))[:limit]:
            lines.append(T(f"- {p['what']} — at `{p['where']}`, {p['times']}×, last {p['last_seen']}",
                           f"- {p['what']} — `{p['where']}` lo, {p['times']} sarlu, last {p['last_seen']}"))
        return "\n".join(lines)

    def visitors_part():
        if not vis:
            return ""
        top = max(vis, key=lambda x: x.get("times", 0))
        n = sum(p.get("times", 0) for p in vis)
        return T(f"Visitors had {n} failed tries (most common: \"{top['what']}\"). These are usually private posts, wrong links or Instagram limits, not bugs.",
                 f"Visitors ki {n} sarlu fail ayyindi (ekkuva ga: \"{top['what']}\"). Ivi sadharanam ga private posts, wrong links leda Instagram limit valla, bugs kaavu.")

    def msgs_part():
        if not msgs:
            return ""
        if not unread:
            return T("📩 No new messages.", "📩 Kotha messages levu.")
        lat = [m for m in msgs.get("latest", []) if not m.get("read")][:3]
        s = T(f"📩 **{unread} new message(s)** waiting:", f"📩 **{unread} kotha message(s)** wait chestunnai:")
        for m in lat:
            s += f"\n- {m.get('topic')} — {m.get('name')} ({m.get('when')}): \"{(m.get('message') or '')[:90]}\""
        return s + T("\nOpen **Messages** and tap **Draft a reply**.", "\n**Messages** lo **Draft a reply** nokkite nenu reply rasi istanu.")

    def dl_part():
        if not dl:
            return T("📊 No download activity recorded yet today.", "📊 Ivala inka downloads record avvaledu.")
        rate = round(100 * (req - fail) / req) if req else 100
        busiest = max(dl.items(), key=lambda kv: kv[1].get("requests", 0))[0] if dl else "-"
        s = T(f"📊 Downloads (today + yesterday): **{req:,}** requests, {rate}% worked. Busiest: {busiest}.",
              f"📊 Downloads (ivala + ninna): **{req:,}** requests, {rate}% success. Ekkuva: {busiest}.")
        al = snap.get("download_alerts_now") or []
        if al:
            s += T("\n🚨 Alert now: " + al[0], "\n🚨 Ippudu alert: " + al[0])
        return s

    def check_part():
        if not hc:
            return ""
        s = T(f"🩺 Last site check ({hc.get('when')}): score {hc.get('score', '-')}, {hc.get('errors', 0)} error(s).",
              f"🩺 Last site check ({hc.get('when')}): score {hc.get('score', '-')}, {hc.get('errors', 0)} error(s).")
        if hc.get("failed_checks"):
            s += "\n- " + "\n- ".join(hc["failed_checks"][:3])
        return s

    def first_fix():
        if real:
            p = max(real, key=lambda x: x.get("times", 0))
            return T(f"👉 Fix first: **{p['what']}** at `{p['where']}` (it happened most).", f"👉 Mundu fix cheyalsindi: **{p['what']}** (`{p['where']}`), idi ekkuva sarlu vachindi.")
        if unread:
            return T("👉 Nothing is broken. Best use of time: reply to the new messages.", "👉 Emi break avvaledu. Kotha messages ki reply ivvandi chalu.")
        if rep:
            return T(f"👉 {rep} reported upload(s) wait for your review in Ringtones → Admin.", f"👉 {rep} reported upload(s) Ringtones → Admin lo review kosam unnai.")
        return T("👉 All good. Nothing needs you right now. 🎉", "👉 Anni bagunnai andi. Ippudu meeru cheyalsindi emi ledu 🎉")

    if re.search(r"\b(thanks|thank you|thank u|tq|thx|dhanyavadalu|super|good job)\b", qn):
        return T("You're welcome! I'll keep watching the site. 🙂", "Welcome andi! Nenu site ni chustune untanu 🙂")
    if re.search(r"\b(error|errors|bug|bugs|broke|broken|problem|problems|issue|issues|crash|fail|failed|emi ayyindi|em jarigindi|enti problem|samasya)\b", qn) and not re.search(r"\b(fix first|mundu|priority|first)\b", qn):
        parts = [bugs_part(6), visitors_part()]
        if real:
            parts.append(T("Open **Error log** and tap **Explain** on any card for the details.", "Details kosam **Error log** lo card meeda **Explain** nokkandi."))
        return "\n\n".join(p for p in parts if p)
    if re.search(r"\b(message|messages|msg|msgs|contact|reply|replies|inbox|mail)\b", qn):
        return msgs_part() or T("📩 No messages yet.", "📩 Inka messages levu.")
    if re.search(r"\b(download|downloads|stats|statistics|traffic|visitors|users|entha mandi|requests|usage)\b", qn):
        return dl_part()
    if re.search(r"\b(fix first|first|priority|mundu|emi cheyali|what should i|todo|to do|next)\b", qn):
        return "\n\n".join(p for p in [bugs_part(3), first_fix()] if p)
    if re.search(r"\b(report|repot|reprt)\b", qn):
        pass                                                  # full report below
    elif re.search(r"\b(hi|hello|hey|namaskaram|namaste)\b", qn) and len(qn.split()) <= 3:
        h = datetime.now(IST).hour
        hi = T("Good morning" if h < 12 else "Good afternoon" if h < 17 else "Good evening", "Namaskaram")
        return f"{hi} Naveen{T('!', ' garu!')} 👋 " + T("Here's the quick picture:", "Quick ga cheptanu:") + "\n\n" + "\n\n".join(p for p in [bugs_part(2), msgs_part(), first_fix()] if p)
    head = T(f"Here's the site report ({snap.get('time_now_india', '')}):", f"Site report andi ({snap.get('time_now_india', '')}):")
    parts = [head, bugs_part(), visitors_part(), msgs_part(), dl_part(), check_part()]
    if rep:
        parts.append(T(f"🎵 {rep} reported upload(s) waiting in Ringtones → Admin.", f"🎵 {rep} reported upload(s) Ringtones → Admin lo unnai."))
    parts.append(first_fix())
    out = "\n\n".join(p for p in parts if p)
    if not re.search(r"\b(report|status|ela undi|ela undhi|how is|how's|today|eeroju|ivala|summary|update|site)\b", qn):
        out += "\n\n" + T("You can also ask me: \"what broke?\", \"any messages?\", \"downloads?\" or \"what should I fix first?\"",
                          "Meeru ilaa kuda adagochu: \"emi break ayyindi?\", \"messages unnaya?\", \"downloads entha?\", \"mundu emi fix cheyali?\"")
    return out


# ------------------------------------------------------------------ contact reply drafts
DRAFTS = {
    "en": {"hi": "Hi {name},", "thanks": "Thank you for writing to WaveRise.",
           "Bug report": "Sorry for the trouble, and thanks for telling us. We're looking into it.",
           "Feature idea": "That's a lovely idea. I've noted it for our next updates.",
           "DMCA / copyright": "We've received your request. Our team will review it and reply to you soon.",
           "Question": "Happy to help.", "other": "We've read your message.",
           "tip": "Meanwhile, this usually helps:", "end": "Naveen, WaveRise"},
    "tel": {"hi": "Namaskaram {name} garu,", "thanks": "WaveRise ki message pettinanduku thanks.",
            "Bug report": "Ibbandi ayyinanduku sorry andi, cheppinanduku thanks. Memu chustunnamu.",
            "Feature idea": "Chala manchi idea andi. Next updates kosam note chesukunnamu.",
            "DMCA / copyright": "Mee request andindi. Team review chesi twaraga reply istaru.",
            "Question": "Tappakunda help chestanu.", "other": "Mee message chadivamu.",
            "tip": "Inthalo idi try cheyandi:", "end": "Naveen, WaveRise"},
    "te": {"hi": "నమస్కారం {name} గారు,", "thanks": "WaveRise కి మెసేజ్ పంపినందుకు ధన్యవాదాలు.",
           "Bug report": "ఇబ్బంది అయినందుకు క్షమించండి, చెప్పినందుకు ధన్యవాదాలు. మేము చూస్తున్నాము.",
           "Feature idea": "చాలా మంచి ఆలోచన. తదుపరి అప్‌డేట్స్ కోసం నోట్ చేసుకున్నాము.",
           "DMCA / copyright": "మీ అభ్యర్థన అందింది. మా టీమ్ పరిశీలించి త్వరలో జవాబిస్తుంది.",
           "Question": "తప్పకుండా సహాయం చేస్తాను.", "other": "మీ మెసేజ్ చదివాము.",
           "tip": "ఈలోపు ఇది ప్రయత్నించండి:", "end": "Naveen, WaveRise"},
    "hi": {"hi": "नमस्ते {name} जी,", "thanks": "WaveRise को लिखने के लिए धन्यवाद।",
           "Bug report": "परेशानी के लिए माफ़ी, और बताने के लिए शुक्रिया। हम इसे देख रहे हैं।",
           "Feature idea": "बहुत अच्छा सुझाव है। अगले अपडेट्स के लिए नोट कर लिया है।",
           "DMCA / copyright": "आपका अनुरोध मिल गया है। हमारी टीम जाँच करके जल्द जवाब देगी।",
           "Question": "ज़रूर मदद करूँगा।", "other": "आपका संदेश पढ़ लिया है।",
           "tip": "तब तक यह आज़माइए:", "end": "Naveen, WaveRise"},
    "hil": {"hi": "Namaste {name} ji,", "thanks": "WaveRise ko message karne ke liye shukriya.",
            "Bug report": "Pareshani ke liye sorry, batane ke liye thanks. Hum dekh rahe hain.",
            "Feature idea": "Bahut accha idea hai. Agle updates ke liye note kar liya.",
            "DMCA / copyright": "Aapki request mil gayi hai. Team review karke jaldi reply karegi.",
            "Question": "Zaroor help karunga.", "other": "Aapka message padh liya.",
            "tip": "Tab tak ye try kijiye:", "end": "Naveen, WaveRise"},
    "ta": {"hi": "வணக்கம் {name},", "thanks": "WaveRise-க்கு எழுதியதற்கு நன்றி.",
           "Bug report": "சிரமத்திற்கு மன்னிக்கவும், சொன்னதற்கு நன்றி. நாங்கள் பார்க்கிறோம்.",
           "Feature idea": "நல்ல யோசனை. அடுத்த அப்டேட்டுகளுக்காக குறித்துக்கொண்டோம்.",
           "DMCA / copyright": "உங்கள் கோரிக்கை கிடைத்தது. எங்கள் குழு பரிசீலித்து விரைவில் பதில் அளிக்கும்.",
           "Question": "கண்டிப்பாக உதவுகிறேன்.", "other": "உங்கள் செய்தியைப் படித்தோம்.",
           "tip": "அதுவரை இதை முயலுங்கள்:", "end": "Naveen, WaveRise"},
}
DRAFTS["tal"] = DRAFTS["en"]


def draft(msg):
    text = str(msg.get("message") or "")
    st = style_of(text, msg.get("lang") or "en")
    if st not in DRAFTS:
        st = "en"
    D = DRAFTS[st]
    name = (msg.get("name") or "").split(" ")[0] or {"en": "there", "tel": "", "te": "", "hi": "", "hil": "", "ta": ""}.get(st, "")
    hi = D["hi"].format(name=name).replace("  ", " ").replace(" ,", ",")
    if not name:
        hi = {"en": "Hi,", "tel": "Namaskaram andi,", "te": "నమస్కారం,", "hi": "नमस्ते,", "hil": "Namaste,", "ta": "வணக்கம்,"}.get(st, "Hi,")
    topic = msg.get("topic") or ""
    key = next((k for k in ("Bug report", "Feature idea", "DMCA / copyright", "Question") if k.split()[0].lower() in topic.lower()), "other")
    lines = [hi, "", D["thanks"] + " " + D[key]]
    if key in ("Bug report", "Question", "other"):
        ans, go = visitor(text, [], body_lang(st) if st in ("te", "hi", "ta") else "en")
        tools = [t for t in detect_tools(norm(text)) if t in GUIDE]
        if tools and tools[0] not in ("video", "facebook", "x", "threads", "reels", "photo", "carousel") and (not go or go[0] != tools[0] or "private" in (ans or "")):
            ans, go = steps_for(tools[0], norm(text), st if st in ("en", "tel", "hil") else "en"), [tools[0]]
        tip = strip_md(ans or "")
        tip = re.sub(r"^(Sorry about that!|Ayyo, sorry andi\.|Arre, sorry ji\.)\s*", "", tip)
        if ans and not tip.startswith(tuple(u[:20] for v in UNSURE.values() for u in v)):
            tip = re.sub(r"\n{2,}.*$", "", tip, flags=re.S) if len(tip) > 500 else tip
            tip = "\n".join(l for l in tip.splitlines() if not re.search(r"Anything else|Let me know|Tell me if|emaina doubt|cheppandi andi!|batao 🙂|kelunga", l)).strip()
            if tip:
                lines += ["", D["tip"], tip]
        for k in go[:1]:
            seo, path_of = _seo()
            if k in seo:
                lines.append("waverise.in" + path_of(k))
    lines += ["", D["end"]]
    return "\n".join(lines).strip()
