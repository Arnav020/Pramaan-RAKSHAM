"""Telegram channel: @PramaanVerifyDemoBot.

Long polling (no public webhook needed), so the bot works from a laptop or EC2.
Flow per item: instant "Checking..." reply -> run check -> certificate card
with action buttons. Coarse verdicts only, per-chat rate limit (oracle abuse).
"""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import time
from pathlib import Path

import httpx

from app.config import get_settings
from app.explain import complaint
from app.models import ContactType, VerdictLevel
from app.render.card import renderer
from app.service import Record, check_and_record
from app.store import db

log = logging.getLogger("pramaan.telegram")
API = "https://api.telegram.org"
MAX_FILE = 20 * 1024 * 1024          # Bot API download limit
RATE_LIMIT = (12, 10)                # at most 12 checks per 10 minutes per chat

TXT = {
    "start": ("<b>Pramaan Verify</b> (hackathon demo)\n\nForward me any suspicious financial message: a video, voice note, screenshot, PDF notice, "
              "UPI ID or link. I check <b>who</b> really sent it, whether it was <b>changed</b>, and <b>what</b> it asks you to do, and reply with a "
              "signed certificate.\n\n<i>Faces can be faked. Payment rails can't.</i>\n\n/lang to switch to हिंदी · /privacy · /help",
              "<b>प्रमाण वेरिफ़ाई</b> (हैकथॉन डेमो)\n\nकोई भी संदिग्ध वित्तीय मैसेज भेजें: वीडियो, वॉइस नोट, स्क्रीनशॉट, PDF नोटिस, UPI आईडी या लिंक। "
              "मैं जाँचता हूँ कि इसे <b>किसने</b> भेजा, क्या इसे <b>बदला</b> गया, और यह आपसे <b>क्या</b> करवाना चाहता है, और एक हस्ताक्षरित प्रमाणपत्र भेजता हूँ।\n\n"
              "/lang से English · /privacy · /help"),
    "call": ("📞 <b>On a call right now?</b>\n\nType what the caller is saying, and the number calling you. For example:\n"
             "<i>\"He says he is from CBI, my Aadhaar is linked to a parcel with drugs, I must transfer ₹50,000 to clear my name. Calling from 9876543210.\"</i>\n\n"
             "Or send a voice note of what they said. I check <b>who they claim to be</b>, <b>the number</b> and <b>what they ask for</b>. "
             "No app can listen to your calls, so I never do.\n\n<b>Never share an OTP or PIN while you wait.</b>",
             "📞 <b>अभी कॉल पर हैं?</b>\n\nकॉल करने वाला जो कह रहा है वह लिखें, और कौन-सा नंबर कॉल कर रहा है। जैसे:\n"
             "<i>\"वह कहता है कि वह CBI से है, मेरे आधार से ड्रग्स वाला पार्सल जुड़ा है, नाम साफ़ करने के लिए ₹50,000 भेजने होंगे। नंबर 9876543210।\"</i>\n\n"
             "या उनकी बात का वॉइस नोट भेजें। मैं जाँचता हूँ कि वे <b>कौन होने का दावा</b> करते हैं, <b>नंबर</b> क्या है और <b>क्या माँगते</b> हैं। "
             "कोई ऐप आपकी कॉल नहीं सुन सकता, इसलिए मैं कभी नहीं सुनता।\n\n<b>इंतज़ार करते समय OTP या PIN कभी न बताएँ।</b>"),
    "trust_ask": ("🤝 <b>Ask someone you trust before you act.</b>\nForward the next message to your son, daughter or a friend. "
                  "When they open it, they see this check and can call you. (The link works once, for 24 hours.)",
                  "🤝 <b>कुछ करने से पहले किसी भरोसेमंद से पूछें।</b>\nअगला मैसेज अपने बेटे, बेटी या दोस्त को फ़ॉरवर्ड करें। "
                  "वे इसे खोलकर यह जाँच देख सकेंगे और आपको कॉल कर सकेंगे। (लिंक एक बार, 24 घंटे तक चलेगा।)"),
    "appeal_sent": ("⚖️ Thank you. A bank analyst will review this verdict. If it was wrong, the rules are corrected for everyone. (We don't store who asked.)",
                    "⚖️ धन्यवाद। बैंक विश्लेषक इस नतीजे की जाँच करेंगे। अगर यह ग़लत था, तो नियम सबके लिए सुधारे जाएँगे। (हम यह नहीं रखते कि किसने पूछा।)"),
    "trust_expired": ("This link has expired or was already used. Ask them to send a new one.", "यह लिंक पुराना हो गया है। उनसे नया लिंक भेजने को कहें।"),
    "trust_told": ("✅ <b>Your trusted contact has seen this check and is calling you now.</b>\nPlease don't pay or share any code until you've spoken.",
                   "✅ <b>आपके भरोसेमंद व्यक्ति ने यह जाँच देख ली है और अभी आपको कॉल कर रहे हैं।</b>\nबात होने तक भुगतान न करें, कोई कोड न बताएँ।"),
    "trust_thanks": ("Thank you. They've been told you're calling. Ask them not to pay or share any code.",
                     "धन्यवाद। उन्हें बता दिया गया है कि आप कॉल कर रहे हैं। उनसे कहें कि भुगतान न करें और कोई कोड न बताएँ।"),
    "help": ("I can check: videos (I find the genuine original and compare the words), text messages, UPI IDs, phone numbers, links and SMS sender IDs "
             "(against official records such as SEBI's @valid UPI rule, .bank.in domains, 1600-series numbers and RBI's Alert List), and the ask "
             "(OTP requests, payments under pressure, app installs).\n\nI never block payments and never call anything 'fake'.",
             "मैं जाँच सकता हूँ: वीडियो (असली मूल वीडियो ढूँढकर शब्दों की तुलना), मैसेज, UPI आईडी, फ़ोन नंबर, लिंक और SMS हेडर (SEBI के @valid UPI नियम, "
             ".bank.in डोमेन, 1600 नंबर और RBI अलर्ट लिस्ट जैसे आधिकारिक रिकॉर्ड से), और माँग (OTP, दबाव में भुगतान, ऐप इंस्टॉल)।"),
    "privacy": ("<b>What I keep:</b> only the item you send, deleted after 24 hours unless you report it, plus an anonymous reference to this chat. "
                "If you tap 'Watch this for me', I keep this chat's address for 30 days only to send you an update.\n<b>Never:</b> your contacts, "
                "other messages, location or bank details. Demo uses test data only.",
                "<b>मैं क्या रखता हूँ:</b> सिर्फ़ आपका भेजा आइटम, 24 घंटे में मिटा दिया जाता है (जब तक आप रिपोर्ट न करें)। 'मेरे लिए नज़र रखें' दबाने पर, "
                "अपडेट भेजने के लिए यह चैट 30 दिन तक याद रखी जाती है।\n<b>कभी नहीं:</b> आपके कॉन्टैक्ट, दूसरे मैसेज, लोकेशन या बैंक जानकारी।"),
    "checking": ("🔍 Checking who sent this, whether it was changed, and what it asks… (about 5–20 s)", "🔍 जाँच हो रही है: किसने भेजा, क्या बदला गया, क्या माँगा जा रहा है… (5–20 सेकंड)"),
    "slow": ("Please wait a few minutes before sending more. This limit stops people from testing scams against Pramaan.",
             "कृपया कुछ मिनट रुकें। यह सीमा लोगों को प्रमाण पर धोखाधड़ी आज़माने से रोकती है।"),
    "too_big": ("That file is over 20 MB, which Telegram doesn't let bots download. Please send a shorter clip.", "फ़ाइल 20 MB से बड़ी है। कृपया छोटा क्लिप भेजें।"),
    "error": ("Sorry, I couldn't check that. Please try again, or treat it as <b>can't verify</b> and check through an official channel.",
              "माफ़ कीजिए, जाँच नहीं हो सकी। दोबारा कोशिश करें, या इसे <b>पुष्टि नहीं</b> मानकर आधिकारिक माध्यम से जाँचें।"),
    "watching": ("🔔 Watching. If bank analysts confirm this as a scam campaign, I'll message you here. (Kept for 30 days.)",
                 "🔔 नज़र रखी जा रही है। अगर बैंक विश्लेषक इसे धोखाधड़ी अभियान मानते हैं, तो मैं आपको यहीं बताऊँगा। (30 दिन तक)"),
    "official": ("<b>Check it yourself, through official channels only</b> (never use numbers or links from the message):\n"
                 "• SEBI register of advisers and brokers: https://www.sebi.gov.in/sebiweb/other/OtherAction.do?doRecognised=yes\n"
                 "• Your bank: the number printed on the back of your card, or its official .bank.in website\n"
                 "• Report a suspected fraud message: Sanchar Saathi (Chakshu) https://sancharsaathi.gov.in\n"
                 "• Already paid? Call <b>1930</b> or report at https://cybercrime.gov.in",
                 "<b>ख़ुद जाँचें, सिर्फ़ आधिकारिक माध्यम से</b> (मैसेज में दिए नंबर या लिंक कभी इस्तेमाल न करें):\n"
                 "• SEBI सलाहकार/ब्रोकर रजिस्टर: https://www.sebi.gov.in/sebiweb/other/OtherAction.do?doRecognised=yes\n"
                 "• आपका बैंक: कार्ड के पीछे लिखा नंबर, या उसकी आधिकारिक .bank.in वेबसाइट\n"
                 "• संदिग्ध मैसेज की रिपोर्ट: संचार साथी (चक्षु) https://sancharsaathi.gov.in\n"
                 "• पैसे भेज चुके हैं? <b>1930</b> पर कॉल करें या https://cybercrime.gov.in पर रिपोर्ट करें"),
}


def html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def t(key: str, lang: str) -> str:
    en, hi = TXT[key]
    return hi if lang == "hi" else en


CALL_BUTTON = ("📞 I'm on a call right now", "📞 मैं अभी कॉल पर हूँ")
CALL_MODE_MINUTES = 10

# On a call, the check is the same; only the first thing we say changes: hang up.
CALL_HEADER = {
    "RED": ("📵 <b>Hang up now.</b> This call matches a known scam pattern.", "📵 <b>अभी फ़ोन काटें।</b> यह कॉल जाने-माने धोखाधड़ी पैटर्न से मेल खाती है।"),
    "AMBER": ("📵 <b>Don't act on this call.</b> Hang up and call your bank on the number printed on your card.",
              "📵 <b>इस कॉल पर कुछ न करें।</b> फ़ोन काटें और कार्ड के पीछे लिखे नंबर पर बैंक को कॉल करें।"),
    "GREEN": ("☎️ This matches an official channel. Still, never share an OTP or PIN on a call.",
              "☎️ यह आधिकारिक माध्यम से मेल खाता है। फिर भी कॉल पर OTP या PIN कभी न बताएँ।"),
}
CALL_TIP = ("<i>Real police, CBI, RBI or bank staff never ask you to pay, share an OTP, install an app or stay on a video call.</i>",
            "<i>असली पुलिस, CBI, RBI या बैंक कभी पैसे, OTP, ऐप इंस्टॉल या वीडियो कॉल पर बने रहने को नहीं कहते।</i>")


def _just_an_identifier(text: str, rec: Record) -> bool:
    """The whole message is one UPI ID, phone number or link (with at most a couple of extra words)."""
    cps = rec.verdict.extraction.contact_points
    if len(cps) != 1 or not text or len(text) > 140:
        return False
    rest = text.lower().replace(cps[0].raw.lower(), " ")
    return len([w for w in rest.split() if len(w) > 2]) <= 3


def v_level(rec: Record) -> str:
    return rec.verdict.level.value


def golden_hour_keyboard(rec: Record, lang: str) -> dict:
    hi = lang == "hi"
    return {"inline_keyboard": [
        [{"text": "📞 1930 पर क्या कहें" if hi else "📞 What to say on 1930", "callback_data": f"cs:{rec.id}"}],
        [{"text": "📝 शिकायत (NCRP)" if hi else "📝 Complaint text (NCRP)", "callback_data": f"ncrp:{rec.id}"},
         {"text": "✉️ बैंक को ईमेल" if hi else "✉️ Email to my bank", "callback_data": f"bem:{rec.id}"}],
        [{"text": "📄 पूरा पैकेट (फ़ाइल)" if hi else "📄 Full packet (file)", "callback_data": f"pkt:{rec.id}"}],
    ]}


def golden_hour(rec: Record, lang: str) -> str:
    upis = [cp.value for cp in rec.verdict.extraction.contact_points if cp.type == ContactType.UPI]
    accs = [cp.value.split("|")[0] for cp in rec.verdict.extraction.contact_points if cp.type == ContactType.BANK_ACCOUNT]
    dest = ", ".join(upis + accs) or ("(the UPI ID or account you paid)" if lang != "hi" else "(जिस UPI आईडी/खाते में भेजा)")
    if lang == "hi":
        return ("<b>⏱ गोल्डन आवर किट</b>\nजितनी जल्दी रिपोर्ट, पैसे रुकने की उतनी ज़्यादा संभावना।\n\n"
                "1. <b>अभी 1930 पर कॉल करें</b> (राष्ट्रीय साइबर अपराध हेल्पलाइन)\n2. अपने बैंक को कार्ड के पीछे लिखे नंबर पर बताएँ\n"
                "3. https://cybercrime.gov.in पर शिकायत दर्ज करें\n\n<b>ये तैयार रखें:</b>\n"
                f"• पैसे पाने वाला: <code>{dest}</code>\n• UTR / ट्रांज़ैक्शन आईडी\n• रकम और समय\n• इस बातचीत के स्क्रीनशॉट\n"
                f"• प्रमाण प्रमाणपत्र: <code>{rec.serial}</code>\n\nहम पैसे वापस मिलने का वादा नहीं कर सकते, पर जल्दी रिपोर्ट से मदद मिलती है।\n"
                "<i>नीचे तैयार ड्राफ़्ट हैं। खाली जगहें ख़ुद भरें: हम आपका UTR या बैंक जानकारी कभी नहीं माँगते।</i>")
    return ("<b>⏱ Golden-Hour Kit</b>\nThe sooner you report, the better the chance the money can be frozen.\n\n"
            "1. <b>Call 1930 now</b> (national cybercrime helpline)\n2. Tell your bank, on the number printed on your card\n"
            "3. File a complaint at https://cybercrime.gov.in\n\n<b>Have these ready:</b>\n"
            f"• Paid to: <code>{dest}</code>\n• UTR / transaction ID\n• Amount and time\n• Screenshots of this chat\n"
            f"• Pramaan certificate: <code>{rec.serial}</code>\n\nWe cannot promise recovery, but fast reports help banks act.\n"
            "<i>Tap below for ready-to-paste drafts. Fill in the blanks yourself: we never ask for your UTR or bank details.</i>")


class Bot:
    def __init__(self, token: str) -> None:
        self.base = f"{API}/bot{token}"
        self.file_base = f"{API}/file/bot{token}"
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=15))
        self.public = get_settings().public_base_url.startswith("https://")
        self.username = "PramaanVerifyDemoBot"
        self.call_mode: dict[int, float] = {}     # chat -> expiry (monotonic): the next item is something said on a call

    async def call(self, method: str, **params):
        files = params.pop("_files", None)
        r = await self.http.post(f"{self.base}/{method}", data=params if files else None, json=None if files else params, files=files)
        j = r.json()
        if not j.get("ok"):
            log.warning("telegram %s failed: %s", method, j.get("description"))
        return j.get("result")

    # ------------------------------------------------------------- helpers
    def chat_lang(self, chat_id: int, user_lang: str | None = None) -> str:
        ref = db.pseudonym("telegram", chat_id)
        lang = db.get_lang(ref)
        if lang:
            return lang
        return "hi" if (user_lang or "").startswith("hi") else "en"

    def keyboard(self, rec: Record, lang: str) -> dict:
        hi = lang == "hi"
        """Four clear actions; everything else sits under ⋯ More."""
        rows = []
        if self.public:
            rows.append([{"text": "🔎 सबूत देखें" if hi else "🔎 See the proof", "url": rec.proof_url}])
        rows.append([{"text": "👪 परिवार" if hi else "👪 Family", "callback_data": f"family:{rec.id}"},
                     {"text": "💸 पैसे भेज चुका हूँ" if hi else "💸 I already paid", "callback_data": f"paid:{rec.id}"}])
        rows.append([{"text": "🚩 बैंक को रिपोर्ट करें" if hi else "🚩 Report to the bank", "callback_data": f"rep:{rec.id}"},
                     {"text": "⋯ और" if hi else "⋯ More", "callback_data": f"more:{rec.id}"}])
        return {"inline_keyboard": rows}

    def more_keyboard(self, rec: Record, lang: str) -> dict:
        hi = lang == "hi"
        return {"inline_keyboard": [
            [{"text": "✅ आधिकारिक जाँच" if hi else "✅ Check officially", "callback_data": f"off:{rec.id}"},
             {"text": "🔔 नज़र रखें" if hi else "🔔 Watch this for me", "callback_data": f"watch:{rec.id}"}],
            [{"text": "⚖️ यह नतीजा ग़लत लगता है" if hi else "⚖️ This looks wrong", "callback_data": f"appeal:{rec.id}"},
             {"text": "English" if hi else "हिंदी", "callback_data": f"lang:{rec.id}"}]]}

    def family_keyboard(self, rec: Record, lang: str) -> dict:
        hi = lang == "hi"
        rows = []
        if v_level(rec) != "GREEN":
            rows.append([{"text": "🤝 पैसे भेजने से पहले पूछें" if hi else "🤝 Ask before I pay", "callback_data": f"trust:{rec.id}"}])
        rows.append([{"text": "📣 उन्हें सावधान करें" if hi else "📣 Warn them", "callback_data": f"fam:{rec.id}"}])
        return {"inline_keyboard": rows}

    def caption(self, rec: Record, lang: str, on_call: bool = False) -> str:
        v = rec.verdict
        icon = {"RED": "🔴", "AMBER": "🟡", "GREEN": "🟢"}[v.level.value]
        title = v.title_hi if lang == "hi" else v.title_en
        lines = []
        if on_call:
            en, hi_ = CALL_HEADER[v.level.value]
            lines += [hi_ if lang == "hi" else en, ""]
        lines.append(f"{icon} <b>{title}</b>")
        if v.amber_kind in ("high_risk", "nothing_to_check") and not on_call:
            lines.append(f"<i>{v.advice_hi if lang == 'hi' else v.advice_en}</i>")
        for i, r in enumerate(v.reasons[:3], 1):
            lines.append(f"{i}. {r.text_hi if lang == 'hi' else r.text_en}")
        if on_call:
            lines.append("\n" + CALL_TIP[1 if lang == "hi" else 0])
        if not self.public:
            lines.append(f"\nProof: {rec.proof_url}")
        lines.append(f"\n<code>{rec.serial}</code> · {'स्कैन कर असली होने की जाँच करें' if lang == 'hi' else 'scan the QR to verify this card'}")
        return "\n".join(lines)[:1024]

    async def download(self, file_id: str, dest_dir: Path, suffix: str) -> Path | None:
        info = await self.call("getFile", file_id=file_id)
        if not info or info.get("file_size", 0) > MAX_FILE:
            return None
        r = await self.http.get(f"{self.file_base}/{info['file_path']}")
        r.raise_for_status()
        p = dest_dir / f"tg_{file_id[-10:]}{suffix}"
        p.write_bytes(r.content)
        return p

    # ------------------------------------------------------------ handlers
    async def on_message(self, m: dict) -> None:
        chat_id = m["chat"]["id"]
        lang = self.chat_lang(chat_id, m.get("from", {}).get("language_code"))
        text = m.get("text") or m.get("caption") or ""
        if text.strip() in CALL_BUTTON:
            text = "/call"
        if text.startswith("/"):
            cmd, _, arg = text.partition(" ")
            cmd = cmd.split("@")[0].lower()
            if cmd == "/start" and arg.startswith("t_"):
                await self.open_trust(chat_id, lang, arg[2:].strip())
            elif cmd == "/call":
                self.call_mode[chat_id] = time.monotonic() + CALL_MODE_MINUTES * 60
                await self.call("sendMessage", chat_id=chat_id, text=t("call", lang), parse_mode="HTML")
            elif cmd in ("/start", "/help", "/privacy"):
                await self.call("sendMessage", chat_id=chat_id, text=t(cmd[1:], lang), parse_mode="HTML", disable_web_page_preview=True,
                                reply_markup=json.dumps(self.main_keyboard(lang)))
            elif cmd == "/lang":
                new = "en" if lang == "hi" else "hi"
                db.set_lang(db.pseudonym("telegram", chat_id), new)
                await self.call("sendMessage", chat_id=chat_id, text="भाषा: हिंदी" if new == "hi" else "Language: English")
            return
        ref = db.pseudonym("telegram", chat_id)
        if db.recent_count(ref, RATE_LIMIT[1]) >= RATE_LIMIT[0]:
            await self.call("sendMessage", chat_id=chat_id, text=t("slow", lang))
            return
        await self.call("sendChatAction", chat_id=chat_id, action="typing")
        status = await self.call("sendMessage", chat_id=chat_id, text=t("checking", lang), reply_to_message_id=m["message_id"])
        with tempfile.TemporaryDirectory(prefix="pramaan_tg_") as td:
            try:
                media = []
                for key, suffix in (("video", ".mp4"), ("video_note", ".mp4"), ("voice", ".ogg"), ("audio", ".mp3"), ("animation", ".mp4")):
                    if key in m:
                        p = await self.download(m[key]["file_id"], Path(td), suffix)
                        if p is None:
                            await self.call("editMessageText", chat_id=chat_id, message_id=status["message_id"], text=t("too_big", lang))
                            return
                        media.append(p)
                if "photo" in m:
                    p = await self.download(m["photo"][-1]["file_id"], Path(td), ".jpg")
                    if p:
                        media.append(p)
                if "document" in m:
                    name = m["document"].get("file_name") or "file.bin"
                    p = await self.download(m["document"]["file_id"], Path(td), Path(name).suffix.lower() or ".bin")
                    if p:
                        media.append(p)
                fwd = m.get("forward_origin", {})
                if fwd.get("type") == "channel" and fwd.get("chat", {}).get("title"):
                    text = f"[forwarded from channel: {fwd['chat']['title']}]\n{text}"
                on_call = self.call_mode.pop(chat_id, 0) > time.monotonic()
                rec = await check_and_record(text, media, channel="telegram", chat_raw=chat_id, language_hint="hi" if lang == "hi" else None)
                try:
                    png = await renderer.render(rec, lang)
                except Exception:
                    log.exception("card render failed; sending the verdict as text")
                    png = None
                if png:
                    await self.call("sendPhoto", chat_id=str(chat_id), caption=self.caption(rec, lang, on_call), parse_mode="HTML",
                                    reply_to_message_id=str(m["message_id"]), reply_markup=json.dumps(self.keyboard(rec, lang)),
                                    _files={"photo": (f"{rec.serial}.png", png, "image/png")})
                else:
                    await self.call("sendMessage", chat_id=str(chat_id), text=self.caption(rec, lang, on_call), parse_mode="HTML",
                                    reply_to_message_id=str(m["message_id"]), reply_markup=json.dumps(self.keyboard(rec, lang)),
                                    disable_web_page_preview=True)
                await self.call("deleteMessage", chat_id=chat_id, message_id=status["message_id"])
                if not media and _just_an_identifier(text, rec):
                    await self.send_lookup(chat_id, text, lang)
            except Exception:
                log.exception("check failed")
                await self.call("editMessageText", chat_id=chat_id, message_id=status["message_id"], text=t("error", lang), parse_mode="HTML")

    async def on_callback(self, q: dict) -> None:
        chat_id = q["message"]["chat"]["id"]
        lang = self.chat_lang(chat_id, q.get("from", {}).get("language_code"))
        action, _, check_id = q.get("data", "").partition(":")
        if action == "tc":   # the trusted contact will call: tell the person who asked
            link = db.answer_trust_link(check_id)
            await self.call("answerCallbackQuery", callback_query_id=q["id"])
            if link:
                asker_lang = db.get_lang(db.pseudonym("telegram", link["requester_chat"])) or lang
                await self.call("sendMessage", chat_id=link["requester_chat"], text=t("trust_told", asker_lang), parse_mode="HTML")
            await self.call("sendMessage", chat_id=chat_id, text=t("trust_thanks", lang))
            return
        row = db.get_check(check_id=check_id)
        if not row:
            await self.call("answerCallbackQuery", callback_query_id=q["id"], text="Expired")
            return
        from app.server import record_from_row
        rec = record_from_row(row)
        await self.call("answerCallbackQuery", callback_query_id=q["id"])
        if action == "off":
            await self.call("sendMessage", chat_id=chat_id, text=t("official", lang), parse_mode="HTML", disable_web_page_preview=True)
        elif action == "paid":
            await self.call("sendMessage", chat_id=chat_id, text=golden_hour(rec, lang), parse_mode="HTML", disable_web_page_preview=True,
                            reply_markup=json.dumps(golden_hour_keyboard(rec, lang)))
        elif action == "cs":
            await self.call("sendMessage", chat_id=chat_id, parse_mode="HTML",
                            text=("<b>1930 पर कॉल करें और यह कहें</b> (खाली जगहें भरें):\n" if lang == "hi" else "<b>Call 1930 and say this</b> (fill the blanks):\n")
                            + f"<pre>{html_escape(complaint.call_script(rec, lang))}</pre>")
        elif action == "ncrp":
            await self.call("sendMessage", chat_id=chat_id, parse_mode="HTML", disable_web_page_preview=True,
                            text=("<b>cybercrime.gov.in → Report Cyber Crime → Financial Fraud.</b> विवरण में यह चिपकाएँ:\n" if lang == "hi"
                                  else "<b>cybercrime.gov.in → Report Cyber Crime → Financial Fraud.</b> Paste this into the incident details:\n")
                            + f"<pre>{html_escape(complaint.ncrp_text(rec))}</pre>")
        elif action == "bem":
            subject, body = complaint.bank_email(rec)
            await self.call("sendMessage", chat_id=chat_id, parse_mode="HTML", disable_web_page_preview=True,
                            text=("<b>अपने बैंक को</b> (कार्ड के पीछे लिखा नंबर / आधिकारिक .bank.in ईमेल):\n" if lang == "hi"
                                  else "<b>To your bank</b> (number on your card / its official .bank.in email):\n")
                            + f"<b>Subject:</b> <code>{html_escape(subject)}</code>\n<pre>{html_escape(body)}</pre>")
        elif action == "pkt":
            await self.call("sendDocument", chat_id=str(chat_id),
                            caption="गोल्डन-आवर पैकेट: तीनों ड्राफ़्ट एक फ़ाइल में" if lang == "hi" else "Golden-Hour packet: all three drafts in one file",
                            _files={"document": (f"pramaan-golden-hour-{rec.serial}.txt", complaint.full_packet(rec).encode("utf-8"), "text/plain")})
        elif action == "trust":
            await self.start_trust(chat_id, rec, lang, q.get("from", {}).get("first_name", ""))
        elif action == "more":
            await self.call("sendMessage", chat_id=chat_id, text="⋯ और विकल्प" if lang == "hi" else "⋯ More options",
                            reply_markup=json.dumps(self.more_keyboard(rec, lang)))
        elif action == "family":
            txt = ("👪 <b>परिवार</b>\nपैसे भेजने से पहले किसी भरोसेमंद से पूछें, या परिवार को इस धोखाधड़ी से सावधान करें।" if lang == "hi"
                   else "👪 <b>Family</b>\nAsk someone you trust before you pay, or warn your family about this.")
            await self.call("sendMessage", chat_id=chat_id, text=txt, parse_mode="HTML", reply_markup=json.dumps(self.family_keyboard(rec, lang)))
        elif action == "appeal":
            await asyncio.to_thread(db.add_appeal, rec.id, "via Telegram")
            await self.call("sendMessage", chat_id=chat_id, text=t("appeal_sent", lang))
        elif action == "watch":
            db.add_watcher(rec.id, "telegram", str(chat_id))
            await self.call("sendMessage", chat_id=chat_id, text=t("watching", lang))
        elif action == "rep":
            from app.service import report_check
            r = await asyncio.to_thread(report_check, rec.id, db.pseudonym("telegram", chat_id))
            db.add_watcher(rec.id, "telegram", str(chat_id))   # reporters hear the outcome
            msg = (f"🚩 रिपोर्ट हो गई। सबूत सील कर दिए गए (वॉल्ट #{r['chain_seq']}, अभियान {r['campaign_id']})। बैंक विश्लेषक इसे एक बार सबके लिए जाँचेंगे, और नतीजा आपको यहीं बताया जाएगा।"
                   if lang == "hi" else
                   f"🚩 Reported. Your evidence is sealed (vault record #{r['chain_seq']}, campaign {r['campaign_id']}). A bank analyst reviews it once for everyone, and you'll hear the outcome here.")
            await self.call("sendMessage", chat_id=chat_id, text=msg)
        elif action == "fam":
            # A forwardable warning: the signed card plus one line, no personal data.
            v = rec.verdict
            line = (f"⚠️ प्रमाण जाँच: \"{v.title_hi}\". ऐसे मैसेज पर भुगतान न करें। कार्ड {rec.serial} का QR स्कैन करके जाँचें।" if lang == "hi"
                    else f"⚠️ Pramaan check: \"{v.title_en}\". Don't pay on messages like this. Scan the QR on card {rec.serial} to verify.")
            png = await renderer.render(rec, lang)
            await self.call("sendPhoto", chat_id=str(chat_id), caption=line + ("\n\n(फ़ॉरवर्ड करें ↗)" if lang == "hi" else "\n\n(Forward this ↗)"),
                            _files={"photo": (f"{rec.serial}.png", png, "image/png")})
        elif action == "lang":
            new = "en" if lang == "hi" else "hi"
            db.set_lang(db.pseudonym("telegram", chat_id), new)
            png = await renderer.render(rec, new)
            await self.call("sendPhoto", chat_id=str(chat_id), caption=self.caption(rec, new), parse_mode="HTML",
                            reply_markup=json.dumps(self.keyboard(rec, new)), _files={"photo": (f"{rec.serial}.png", png, "image/png")})

    async def send_lookup(self, chat_id, text: str, lang: str) -> None:
        """Someone sent only a UPI ID, number or link: add what Lookup knows (confirmed campaigns, how often it was checked)."""
        from app.lookup import lookup
        res = await asyncio.to_thread(lookup, text, False)
        if not res.get("value"):
            return
        hi = lang == "hi"
        lines = [f"🔎 <b>{'लुकअप' if hi else 'Lookup'}: <code>{html_escape(res['value'])}</code></b>", res["headline_hi"] if hi else res["headline_en"]]
        for c in res["campaigns"][:2]:
            lines.append(f"• {html_escape(c['title'])} ({'पुष्टि' if hi else 'confirmed'} {c['confirmed_on']})")
        n = res["seen_30d"]
        lines.append((f"पिछले 30 दिनों में {n} जाँचों में यह मिला।" if hi else f"Seen in {n} check{'s' if n != 1 else ''} in the last 30 days."))
        lines.append(f"<i>{res['advice_hi'] if hi else res['advice_en']}</i>")
        await self.call("sendMessage", chat_id=chat_id, text="\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)

    def main_keyboard(self, lang: str) -> dict:
        """The always-visible button under the text box: one tap when a scam call is happening."""
        return {"keyboard": [[{"text": CALL_BUTTON[1 if lang == "hi" else 0]}]], "resize_keyboard": True, "is_persistent": True}

    async def send_card(self, chat_id, rec: Record, lang: str, caption: str, keyboard: dict) -> None:
        try:
            png = await renderer.render(rec, lang)
        except Exception:
            log.exception("card render failed; sending text")
            png = None
        if png:
            await self.call("sendPhoto", chat_id=str(chat_id), caption=caption[:1024], parse_mode="HTML", reply_markup=json.dumps(keyboard),
                            _files={"photo": (f"{rec.serial}.png", png, "image/png")})
        else:
            await self.call("sendMessage", chat_id=str(chat_id), text=caption, parse_mode="HTML", reply_markup=json.dumps(keyboard),
                            disable_web_page_preview=True)

    async def start_trust(self, chat_id: int, rec: Record, lang: str, first_name: str) -> None:
        """The person asks a family member before acting: a one-time link they forward."""
        token = db.new_trust_link(rec.id, "telegram", str(chat_id))
        link = f"https://t.me/{self.username}?start=t_{token}"
        who = (first_name or "").strip()[:32] or ("कोई जानने वाला" if lang == "hi" else "Someone you know")
        title = rec.verdict.title_hi if lang == "hi" else rec.verdict.title_en
        await self.call("sendMessage", chat_id=chat_id, text=t("trust_ask", lang), parse_mode="HTML")
        fwd = (f"🤝 <b>{html_escape(who)} आपसे पूछना चाहते हैं</b>, एक मैसेज पर कुछ करने से पहले।\nप्रमाण का नतीजा: <b>{title}</b>\n\nखोलकर देखें और उन्हें कॉल करें: {link}"
               if lang == "hi" else
               f"🤝 <b>{html_escape(who)} wants your advice</b> before acting on a message.\nPramaan says: <b>{title}</b>\n\nOpen it and call them: {link}")
        await self.call("sendMessage", chat_id=chat_id, text=fwd, parse_mode="HTML", disable_web_page_preview=True,
                        reply_markup=json.dumps({"inline_keyboard": [[{"text": "👀 देखें" if lang == "hi" else "👀 Open the check", "url": link}]]}))

    async def open_trust(self, chat_id: int, lang: str, token: str) -> None:
        link = db.open_trust_link(token)
        row = db.get_check(check_id=link["check_id"]) if link else None
        if not row:
            await self.call("sendMessage", chat_id=chat_id, text=t("trust_expired", lang))
            return
        from app.server import record_from_row
        rec = record_from_row(row)
        head = ("🤝 <b>किसी जानने वाले ने कुछ करने से पहले आपसे यह जाँच देखने को कहा है।</b>\n\n" if lang == "hi"
                else "🤝 <b>Someone you know asked you to look at this before they act on it.</b>\n\n")
        tail = ("\n\n<b>उन्हें अभी कॉल करें।</b> कहें कि भुगतान न करें और कोई कोड न बताएँ।" if lang == "hi"
                else "\n\n<b>Please call them now.</b> Tell them not to pay or share any code.")
        rows = [[{"text": "📞 मैं उन्हें अभी कॉल कर रहा/रही हूँ" if lang == "hi" else "📞 I'll call them now", "callback_data": f"tc:{token}"}]]
        if self.public:
            rows.append([{"text": "🔎 सबूत देखें" if lang == "hi" else "🔎 See the proof", "url": rec.proof_url}])
        await self.send_card(chat_id, rec, lang, head + self.caption(rec, lang) + tail, {"inline_keyboard": rows})

    async def send_text(self, chat_id: str, text: str) -> None:
        await self.call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML", disable_web_page_preview=True)

    async def run(self) -> None:
        me = await self.call("getMe")
        self.username = (me or {}).get("username") or self.username
        log.info("telegram bot @%s polling", self.username)
        await self.call("setMyCommands", commands=[{"command": "start", "description": "How to use Pramaan"},
                                                   {"command": "call", "description": "I'm on a suspicious call right now"},
                                                   {"command": "help", "description": "What Pramaan can check"},
                                                   {"command": "lang", "description": "English / हिंदी"},
                                                   {"command": "privacy", "description": "What we store and for how long"}])
        offset = 0
        while True:
            try:
                r = await self.http.get(f"{self.base}/getUpdates", params={"timeout": 30, "offset": offset,
                                                                          "allowed_updates": '["message","callback_query"]'}, timeout=45)
                for u in r.json().get("result", []):
                    offset = u["update_id"] + 1
                    if "message" in u:
                        asyncio.create_task(self.on_message(u["message"]))
                    elif "callback_query" in u:
                        asyncio.create_task(self.on_callback(u["callback_query"]))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("polling error: %s", type(e).__name__)
                await asyncio.sleep(3)


BOT: Bot | None = None


async def run_bot() -> None:
    global BOT
    BOT = Bot(get_settings().telegram_bot_token)
    await BOT.run()
