"""Multilingual (English / Hindi / Hinglish) rules for the ask and pressure tactics.

Rules give a floor that works with no model at all; the LLM extractor can only
add to what they find, never remove it.
"""

from __future__ import annotations

import re

from app.extract.contacts import normalise_text
from app.models import AskType, Pressure

_F = re.IGNORECASE

ASK_PATTERNS: dict[AskType, re.Pattern] = {
    AskType.PAY: re.compile(
        r"\b(?:pay|send|transfer|deposit|invest|remit)\b.{0,40}?(?:₹|\brs\.?|\binr\b|\brupees?\b|\d{3,}|\bupi\b|@|\baccount\b|\ba/c\b)"
        r"|(?:₹|\brs\.?|\binr)\s?\d[\d,]*.{0,30}?\b(?:pay|send|transfer|deposit|fee|charges?|tax)\b"
        r"|registration\s+fee|processing\s+fee|security\s+deposit|safety\s+deposit|refundable\s+deposit|pay\s+(?:the\s+)?tax\s+to\s+withdraw"
        r"|भुगतान\s*कर|पैसे\s*(?:भेज|ट्रांसफर|जमा)|जमा\s*कर|पेमेंट\s*कर|ट्रांसफर\s*कर|paise\s+(?:bhej|transfer|jama)|payment\s+kar",
        _F,
    ),
    AskType.CREDENTIALS: re.compile(
        r"\b(?:share|send|tell|give|enter|provide|confirm)\b.{0,30}?\b(?:otp|pin|cvv|password|passcode|mpin|upi\s*pin|card\s*number)\b"
        r"|\botp\b.{0,25}?\b(?:share|send|tell|batao|bhejo|bata)\b|ओटीपी|पिन\s*(?:बताएं|बताइए|भेजें)|otp\s*batao",
        _F,
    ),
    AskType.INSTALL_APP: re.compile(
        r"\b(?:download|install)\b.{0,40}?\b(?:app|apk|application|software)\b|\.apk\b|anydesk|teamviewer|quicksupport"
        r"|ऐप\s*(?:डाउनलोड|इंस्टॉल)|app\s+download\s+kar",
        _F,
    ),
    AskType.PRIVATE_GROUP: re.compile(
        r"\b(?:join|add\s+you\s+to)\b.{0,40}?\b(?:group|channel|vip|community|telegram|whatsapp)\b|vip\s+(?:group|club|channel)|t\.me/|chat\.whatsapp\.com"
        r"|ग्रुप\s*(?:ज्वाइन|जॉइन|में\s*जुड़)|group\s+join\s+kar",
        _F,
    ),
    AskType.CLICK_LINK: re.compile(r"\b(?:click|tap|open|visit)\b.{0,25}?\b(?:link|below|here|url)\b|लिंक\s*पर\s*क्लिक|link\s+pe\s+click", _F),
    AskType.REMOTE_ACCESS: re.compile(r"anydesk|teamviewer|quicksupport|screen\s*share|स्क्रीन\s*शेयर", _F),
    AskType.CALL_BACK: re.compile(r"\b(?:call|contact|whatsapp)\b\s+(?:us|me|our|on|at)\b|कॉल\s*कर(?:ें|िए)", _F),
}

PRESSURE_PATTERNS: dict[Pressure, re.Pattern] = {
    Pressure.URGENCY: re.compile(
        r"\b(?:urgent(?:ly)?|immediately|today\s+only|within\s+\d+\s*(?:hours?|hrs?|minutes?|mins?)|last\s+chance|limited\s+(?:seats|slots|time)|expires?\s+today|act\s+now|right\s+now)\b"
        r"|तुरंत|अभी\s*(?:के\s*अभी|तुरंत)|आज\s*ही|सिर्फ़?\s*आज|jaldi|turant|abhi",
        _F,
    ),
    Pressure.SECRECY: re.compile(
        r"\b(?:don'?t|do\s+not)\s+(?:tell|share|inform|disconnect|hang\s+up|cut\s+the\s+call)\b|\btell\s+(?:no\s*one|nobody)\b|\bno\s*one\s+(?:must|should)\s+know\b"
        r"|keep\s+(?:it|this)?\s*(?:a\s+)?(?:secret|confidential)|किसी\s*को\s*(?:मत|न)\s*बता|kisi\s+ko\s+mat\s+batana|कॉल\s*(?:मत|न)\s*काट",
        _F,
    ),
    Pressure.GUARANTEED_RETURNS: re.compile(
        r"guarantee[d]?\s+(?:[\w%]+\s+){0,2}(?:returns?|profits?|income)|assured\s+(?:[\w%]+\s+){0,2}(?:returns?|profits?)|risk[\s-]*free\s+(?:returns?|profit|investment)|\b\d{2,4}\s?%\s+(?:returns?|profit|guaranteed)|(?:returns?|profit)\s+of\s+\d{2,4}\s?%"
        # money-doubling promises, however they are phrased ("get your money doubled in just three days")
        r"|\bdoubl(?:e|es|ed|ing)\s+(?:your\s+|the\s+)?(?:money|investment|amount|capital)\b|\b(?:money|investment|amount|capital)\s+(?:\w+\s+){0,2}doubl(?:e|ed|es)\b"
        r"|\bdoubl(?:e|ed)\s+in\s+(?:just\s+|only\s+)?(?:\d+|one|two|three|four|five|six|seven|ten|a)\s*(?:days?|weeks?|months?|hours?)\b|\b(?:[2-9]|10)x\s+(?:returns?|profit|your\s+money)\b"
        r"|गारंटी(?:ड)?\s*(?:रिटर्न|मुनाफ़?ा)|पैसा\s*(?:डबल|दोगुना|दुगना)|पैसे\s*(?:डबल|दोगुने|दुगने)|दोगुना\s*(?:मुनाफ़?ा|रिटर्न)|paisa\s+double|paise\s+double|guaranteed\s+return",
        _F,
    ),
    Pressure.THREAT: re.compile(
        r"\b(?:arrest(?:ed)?|warrant|fir\b|legal\s+action|account\s+(?:will\s+be\s+)?(?:blocked|suspended|frozen|closed)|money\s+laundering|case\s+registered|penalty|jail)\b"
        # digital-arrest script: a parcel "in your name" holding drugs or fake passports
        r"|\b(?:drugs?|narcotics|contraband|mdma)\b|\bparcel\b.{0,40}?\b(?:seized|illegal|customs|passports?)\b"
        r"|गिरफ़?्तार|वारंट|कानूनी\s*कार्रवाई|अकाउंट\s*(?:ब्लॉक|बंद)|डिजिटल\s*अरेस्ट|ड्रग्स|नशीले",
        _F,
    ),
    Pressure.AUTHORITY: re.compile(r"\b(?:officer|inspector|commissioner|judge|official\s+notice|government\s+of\s+india|by\s+order)\b|अधिकारी|आदेश", _F),
    Pressure.TOO_GOOD: re.compile(r"\b(?:lottery|jackpot|you\s+(?:have\s+)?won|free\s+(?:gift|iphone)|cashback\s+of|reward\s+points?\s+expir)\b|लॉटरी|इनाम", _F),
}

# Text aimed at automated checkers rather than people: a known manipulation trick.
HIDDEN_INSTRUCTION_RE = re.compile(
    r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions?|rules?)|you\s+are\s+(?:now\s+)?(?:an?\s+)?(?:ai|assistant|system)"
    r"|(?:system|assistant)\s*:\s*|mark\s+(?:this|it)\s+as\s+(?:safe|verified|genuine)|classify\s+(?:this|it)\s+as\s+(?:safe|genuine|legitimate)"
    r"|<\s*/?\s*(?:system|instructions?)\s*>|respond\s+with\s+[\"']?(?:safe|verified)",
    _F,
)


# Genuine safety advice says what an institution will NEVER do ("we never ask
# for your OTP"). Such clauses are removed before looking for asks or scam
# patterns, so a bank's warning is not mistaken for the scam it warns about.
_CLAUSE_SPLIT = re.compile(r"(?<=[.!?।\n])\s+|,\s*(?:but|however|lekin|पर|लेकिन)\s+", re.IGNORECASE)
_NEGATION = re.compile(
    r"\b(?:never|will\s+not|won'?t|do(?:es)?\s+not|don'?t|no\s+bank|no\s+one\s+(?:from|at|in|will|would|can|ever)|not\s+ask)\b|कभी\s*(?:भी)?\s*(?:नहीं|न)|नहीं\s*माँग|kabhi\s+nahi"
    # warnings and advice ("anyone who promises guaranteed returns is not telling the truth")
    r"|\b(?:anyone\s+who|whoever|beware|be\s+careful|be\s+cautious|avoid\s+(?:anyone|people|anybody|such|messages|calls)|is\s+not\s+telling|not\s+telling\s+(?:you\s+)?the\s+truth"
    r"|only\s+through\s+official|check\s+that|before\s+you\s+invest|there\s+is\s+no\s+secret|there\s+isn'?t\s+one)\b"
    r"|जो\s*भी|सच\s*नहीं|सावधान|सिर्फ़?\s*आधिकारिक|नहीं\s*(?:माँग|मांग|कहेगा|कहेंगे|भेजेगा)|कभी\s*(?:भी)?\b"
    # official reporting advice ("call 1930 immediately") is guidance, not pressure
    r"|\b1930\b|cybercrime\.gov\.in|sanchar\s*saathi|helpline",
    re.IGNORECASE,
)


# A scammer's own orders are negative too ("don't tell anyone", "tell no one", "do not disconnect").
# Those are pressure, not safety advice, so they don't make a clause count as a warning. A clause that
# talks about OTPs, PINs or passwords stays a warning ("do not share your OTP with anyone").
_SCAM_ORDER = re.compile(
    r"\b(?:don'?t|do\s+not)\s+(?:tell|inform|disconnect|hang\s+up|cut\s+the\s+call|leave\s+the\s+(?:room|call|house))\b(?:\s+(?:anyone|anybody|your\s+family|family|the\s+bank|police))?"
    r"|\btell\s+(?:no\s*one|nobody)\b|\bno\s*one\s+(?:must|should)\s+know\b|किसी\s*को\s*(?:मत|न)\s*बता\S*|कॉल\s*(?:मत|न)\s*काट\S*|kisi\s+ko\s+mat\s+batana",
    re.IGNORECASE,
)
_CREDENTIAL_WORDS = re.compile(r"\b(?:otp|pin|cvv|password|passcode|mpin)\b|ओटीपी|पिन", re.IGNORECASE)


def _is_warning(clause: str) -> bool:
    if _CREDENTIAL_WORDS.search(clause):
        return bool(_NEGATION.search(clause))
    return bool(_NEGATION.search(_SCAM_ORDER.sub(" ", clause)))


def strip_negated(text: str) -> str:
    return " ".join(c for c in _CLAUSE_SPLIT.split(text) if c and not _is_warning(c))


def detect_asks(text: str) -> list[AskType]:
    t = strip_negated(normalise_text(text))
    return [k for k, p in ASK_PATTERNS.items() if p.search(t)]


def detect_pressure(text: str) -> list[Pressure]:
    t = strip_negated(normalise_text(text))
    return [k for k, p in PRESSURE_PATTERNS.items() if p.search(t)]


def has_hidden_instructions(text: str) -> bool:
    return bool(HIDDEN_INSTRUCTION_RE.search(normalise_text(text)))
