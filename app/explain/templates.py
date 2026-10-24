"""Fixed, fact-bearing sentences for every reason code, in English and Hindi.

These templates are the ONLY source of claims in a verdict. An LLM may later
rephrase them for tone or another language, but a post-check rejects any
rephrase that adds numbers, names or links not in the template.
Wording rules: never say "fake" or "fraudster"; say what failed to match.
"""

from __future__ import annotations

from collections import defaultdict

from app.models import Reason, Signal, VerdictLevel

TITLES = {
    VerdictLevel.GREEN: {"en": "Verified sender", "hi": "सत्यापित भेजने वाला"},
    VerdictLevel.AMBER: {"en": "Can't verify. Check before you act", "hi": "पुष्टि नहीं हो सकी। कुछ भी करने से पहले जाँचें"},
    VerdictLevel.RED: {"en": "Matches a known scam pattern. Do not pay", "hi": "जाने-माने धोखाधड़ी पैटर्न से मेल खाता है। भुगतान न करें"},
}
# Amber with nothing to check AND nothing risky (no ask, no pressure, no scam pattern): still not "verified",
# but it must not read like a warning ("Happy birthday!" is not suspicious).
NO_RISK_TITLE = {"en": "No warning signs. Nothing to verify", "hi": "कोई चेतावनी संकेत नहीं। जाँचने को कुछ नहीं"}
# Amber with a high-risk ask (OTP, remote access, a payment pushed with threats or promises, a known scam script):
# still not an accusation (one lane only), but it must read as the warning it is, not as a shrug.
HIGH_RISK_TITLE = {"en": "Warning signs of a scam. Don't pay or share anything", "hi": "धोखाधड़ी के चेतावनी संकेत। न भुगतान करें, न कुछ बताएँ"}

T: dict[str, tuple[str, str]] = {
    # ---- Lane A · WHO -------------------------------------------------------
    "UPI_VALID_HANDLE": ("{upi} is a SEBI-validated '@valid' UPI ID for a {category}.",
                         "{upi} SEBI द्वारा मान्य '@valid' UPI आईडी है ({category})।"),
    "UPI_VALID_HANDLE_NO_CATEGORY": ("{upi} uses the '@valid' handle but without a known SEBI category.",
                                     "{upi} में '@valid' है, पर कोई मान्य SEBI श्रेणी नहीं है।"),
    "UPI_NOT_VALID_FOR_SEBI_CLAIM": ("{upi} is not a SEBI-validated '@valid' UPI ID. SEBI-registered advisers and brokers must collect money through one.",
                                     "{upi} SEBI द्वारा मान्य '@valid' UPI आईडी नहीं है। SEBI-रजिस्टर्ड सलाहकार और ब्रोकर सिर्फ़ ऐसी आईडी से ही पैसे लेते हैं।"),
    "UPI_FOR_REGULATOR": ("RBI, SEBI, police and courts never ask the public to send money to a UPI ID like {upi}.",
                          "RBI, SEBI, पुलिस या कोर्ट कभी भी {upi} जैसी UPI आईडी पर पैसे नहीं मँगवाते।"),
    "UPI_PERSONAL_FOR_BANK": ("Banks do not ask customers to pay into a personal UPI ID like {upi}.",
                              "बैंक ग्राहकों से {upi} जैसी निजी UPI आईडी पर भुगतान नहीं करवाते।"),
    "QR_PAYEE_NOT_CLAIMED_ENTITY": ("The QR code pays {upi} in the name of \"{payee}\", not {claimed}.",
                                    "QR कोड {upi} पर \"{payee}\" के नाम भुगतान करता है, {claimed} को नहीं।"),
    "QR_PAYEE_NAME": ("The QR code pays {upi} in the name of \"{payee}\".", "QR कोड {upi} पर \"{payee}\" के नाम भुगतान करता है।"),
    "UPI_UNVERIFIED": ("We could not link {upi} to any verified business.",
                       "{upi} को किसी सत्यापित संस्था से नहीं जोड़ा जा सका।"),
    "DOMAIN_OFFICIAL": ("The link goes to {domain}, an official domain of {owner}.",
                        "लिंक {domain} पर जाता है, जो {owner} का आधिकारिक डोमेन है।"),
    "DOMAIN_OTHER_INSTITUTION": ("The link goes to {domain}, which belongs to {owner}, not to the bank named in the message.",
                                 "लिंक {domain} पर जाता है, जो {owner} का है, मैसेज में बताए गए बैंक का नहीं।"),
    "DOMAIN_LOOKALIKE": ("{domain} imitates {imitates}, but it is not their official site ({official}).",
                         "{domain} {imitates} जैसा दिखता है, पर यह उनकी आधिकारिक साइट ({official}) नहीं है।"),
    "DOMAIN_PUNYCODE": ("{domain} uses look-alike characters to imitate another address.",
                        "{domain} किसी दूसरे पते की नकल के लिए मिलते-जुलते अक्षरों का इस्तेमाल करता है।"),
    "DOMAIN_ZONE_SPOOF": ("{domain} puts '{zone}' at the front to look official, but the address really belongs to {owner}.",
                          "{domain} आधिकारिक दिखने के लिए आगे '{zone}' लगाता है, पर असल में यह पता {owner} का है।"),
    "DOMAIN_SHORTENER": ("The link is hidden behind a URL shortener ({domain}), so its real destination is unknown.",
                         "लिंक एक शॉर्टनर ({domain}) के पीछे छिपा है, असली पता पता नहीं चलता।"),
    "LINK_TO_PRIVATE_CHAT": ("The link moves you to a private chat ({domain}).",
                             "लिंक आपको एक प्राइवेट चैट ({domain}) पर ले जाता है।"),
    "DOMAIN_NOT_OFFICIAL_FOR_CLAIM": ("{domain} is not an official domain of the institution named. Indian banks now use addresses ending in .bank.in.",
                                      "{domain} बताई गई संस्था का आधिकारिक डोमेन नहीं है। भारतीय बैंक अब .bank.in वाले पते इस्तेमाल करते हैं।"),
    "DOMAIN_GOVERNMENT": ("{domain} is a government address: only Indian government bodies can register names ending in .gov.in or .nic.in.",
                          "{domain} सरकारी पता है: .gov.in या .nic.in वाले नाम सिर्फ़ भारत सरकार की संस्थाएँ ले सकती हैं।"),
    "DOMAIN_NOT_GOV_FOR_GOVT_CLAIM": ("The message claims to be from {claimed}, but the link goes to {domain}. Government websites end in .gov.in or .nic.in.",
                                      "मैसेज ख़ुद को {claimed} का बताता है, पर लिंक {domain} पर जाता है। सरकारी वेबसाइटें .gov.in या .nic.in पर ख़त्म होती हैं।"),
    "DOMAIN_FREE_HOSTING": ("{domain} is a free site on {platform}, which anyone can create in minutes. Banks and registered investment firms don't use free sites.",
                            "{domain} {platform} पर बनी मुफ़्त साइट है, जिसे कोई भी कुछ मिनटों में बना सकता है। बैंक और रजिस्टर्ड निवेश कंपनियाँ मुफ़्त साइटों का इस्तेमाल नहीं करतीं।"),
    "DOMAIN_UNVERIFIED": ("We could not verify who owns {domain}.",
                          "{domain} किसका है, इसकी पुष्टि नहीं हो सकी।"),
    # Pramaan Seal: institutions sign what they really send
    "SEALED": ("Signed by {issuer} with a Pramaan Seal on {issued}: this is exactly the text they sent.",
               "{issuer_hi} ने {issued} को प्रमाण सील से इस पर हस्ताक्षर किए: यह बिल्कुल वही है जो उन्होंने भेजा था।"),
    "SEAL_CONTENT_CHANGED": ("This carries {issuer}'s genuine seal, but the text was changed after signing. Not in what {issuer} sent: {changed}.",
                             "इस पर {issuer_hi} की असली सील है, पर हस्ताक्षर के बाद बदलाव किया गया। {issuer_hi} के भेजे मैसेज में यह नहीं था: {changed}।"),
    "SEAL_FORGED": ("The seal on this does not match {issuer}'s signature: it was forged or tampered with.",
                    "इस पर लगी सील {issuer_hi} के हस्ताक्षर से मेल नहीं खाती: यह नक़ली है या इससे छेड़छाड़ हुई है।"),
    "SEAL_REVOKED": ("{issuer} has withdrawn this seal, so this document should no longer be trusted.",
                     "{issuer_hi} ने यह सील वापस ले ली है, इसलिए इस दस्तावेज़ पर अब भरोसा नहीं करना चाहिए।"),
    "SEAL_NOT_ISSUED": ("The seal code {code} was never issued: the 'verified' seal on this is fake.",
                        "सील कोड {code} कभी जारी ही नहीं हुआ: इस पर लगी 'वेरिफ़ाइड' सील नक़ली है।"),
    "OFFICIAL_ORIGINAL": ("This is {owner}'s own video from its registered official channel ({source_title}), with the words unchanged.",
                          "यह {owner} का अपना वीडियो है, उसके रजिस्टर्ड आधिकारिक चैनल से ({source_title_hi}), और शब्द बदले नहीं गए हैं।"),
    # What we saw when we visited the website's homepage (never the link itself)
    "SITE_NOT_FOUND": ("The website it points to, {domain}, does not exist. A real organisation's website would.",
                       "जिस वेबसाइट {domain} का ज़िक्र है, वह मौजूद ही नहीं है। किसी असली संस्था की वेबसाइट होती।"),
    "SITE_NEW_DOMAIN": ("{domain} was registered only {days} days ago ({registered}). Genuine banks and investment firms don't collect money through brand-new websites.",
                        "{domain} सिर्फ़ {days} दिन पहले ({registered}) रजिस्टर हुई है। असली बैंक और निवेश कंपनियाँ बिल्कुल नई वेबसाइटों से पैसे नहीं लेतीं।"),
    "SITE_IMPERSONATES": ("The website {domain} calls itself {entity}, but it is not {entity}'s official site.",
                          "वेबसाइट {domain} ख़ुद को {entity} बताती है, पर यह {entity} की आधिकारिक साइट नहीं है।"),
    "SITE_ASKS_SECRETS": ("The website {domain} asks you to type your {fields} on its first page.",
                          "वेबसाइट {domain} पहले ही पेज पर आपका {fields} डालने को कहती है।"),
    "SITE_OFFERS_APK": ("The website {domain} offers an app file (.apk) to install from outside the Play Store.",
                        "वेबसाइट {domain} Play Store के बाहर से इंस्टॉल करने के लिए ऐप फ़ाइल (.apk) देती है।"),
    "SITE_REDIRECTS": ("The website {domain} sends visitors on to a different site, {target}.",
                       "वेबसाइट {domain} आने वालों को किसी दूसरी साइट {target} पर भेज देती है।"),
    "SITE_PROMISES_RETURNS": ("The website {domain} itself promises guaranteed returns.",
                              "वेबसाइट {domain} ख़ुद गारंटीड रिटर्न का वादा करती है।"),
    "SITE_FREE_HOSTING": ("The website {domain} is a free site on {platform}, with no registration record of its own, and it {what}. Licensed firms don't do this.",
                          "वेबसाइट {domain} {platform} पर बनी मुफ़्त साइट है, जिसका अपना कोई रजिस्ट्रेशन रिकॉर्ड नहीं है, और यह {what_hi}। लाइसेंस वाली कंपनियाँ ऐसा नहीं करतीं।"),
    "SITE_PLAYBOOK_MATCH": ("The website {domain} itself follows the pattern of a known scam: {name_en}.",
                            "वेबसाइट {domain} ख़ुद एक जानी-मानी धोखाधड़ी जैसी है: {name_hi}।"),
    "SITE_ASK_PAY_WITH_PRESSURE": ("The website {domain} itself asks for money while pushing {pressure_en}.",
                                    "वेबसाइट {domain} ख़ुद {pressure_hi} के सहारे दबाव डालकर पैसे माँगती है।"),
    "PHONE_FRI_HIGH": ("The number {phone} is rated '{risk}' on the fraud-risk indicator (demo data).",
                       "नंबर {phone} को फ़्रॉड-रिस्क इंडिकेटर पर '{risk}' जोखिम बताया गया है (डेमो डेटा)।"),
    "PHONE_MOBILE_FOR_INSTITUTION": ("{phone} is an ordinary mobile number. Banks and financial firms must call customers from 1600-series numbers.",
                                     "{phone} एक आम मोबाइल नंबर है। बैंक और वित्तीय संस्थाएँ ग्राहकों को 1600 से शुरू होने वाले नंबरों से ही कॉल करती हैं।"),
    "PHONE_1600_SERIES": ("{phone} is in the 1600 series reserved for banks and financial firms. Caller ID can still be spoofed.",
                          "{phone} 1600 सीरीज़ में है, जो बैंकों और वित्तीय संस्थाओं के लिए है। फिर भी कॉलर आईडी नकली हो सकती है।"),
    "PHONE_140_PROMO": ("{phone} is a 140-series promotional number.", "{phone} 140 सीरीज़ का प्रचार वाला नंबर है।"),
    "PHONE_TOLL_FREE": ("{phone} is a toll-free number.", "{phone} एक टोल-फ़्री नंबर है।"),
    "PHONE_UNVERIFIED": ("We could not verify who uses {phone}.", "{phone} किसका नंबर है, इसकी पुष्टि नहीं हो सकी।"),
    "DOMAIN_RAW_IP": ("The link goes to a bare internet address ({domain}) with no website name. Banks and registered firms always use their own domain.",
                      "लिंक बिना वेबसाइट नाम वाले सीधे इंटरनेट पते ({domain}) पर ले जाता है। बैंक और पंजीकृत कंपनियाँ हमेशा अपना डोमेन इस्तेमाल करती हैं।"),
    "SMS_HEADER_KNOWN": ("The text names {header}, a real header of {owner}. Anyone can type a header: check the sender line in your SMS app.",
                         "मैसेज में {header} लिखा है, जो {owner} का असली हेडर है। हेडर कोई भी लिख सकता है: अपने SMS ऐप में भेजने वाले की लाइन देखें।"),
    "SMS_HEADER_PROMO": ("{header} is a promotional sender, not a transaction alert.", "{header} प्रचार भेजने वाला हेडर है, लेन-देन का अलर्ट नहीं।"),
    "SMS_HEADER_UNKNOWN": ("The SMS sender {header} is not a header we can match to a bank.", "SMS हेडर {header} को किसी बैंक से नहीं जोड़ा जा सका।"),
    "SEBI_REG_FOUND": ("{reg_no} is in SEBI's register as {name}.", "{reg_no} SEBI के रजिस्टर में {name} के नाम से है।"),
    "SEBI_REG_UNCONFIRMED": ("We could not confirm {reg_no} in SEBI's register. Check it yourself on SEBI's intermediary search.",
                             "{reg_no} की SEBI रजिस्टर में पुष्टि नहीं हो सकी। SEBI की वेबसाइट पर ख़ुद जाँचें।"),
    "EMAIL_FREE_FOR_INSTITUTION": ("{email} is a free personal email address. Banks and regulators write from their own domains.",
                                   "{email} एक मुफ़्त निजी ईमेल है। बैंक और नियामक अपने आधिकारिक डोमेन से ईमेल भेजते हैं।"),
    "ALERT_LIST_HIT": ("'{entity}' is on RBI's Alert List of unauthorised forex trading platforms.",
                       "'{entity}' RBI की अनाधिकृत फ़ॉरेक्स ट्रेडिंग प्लेटफ़ॉर्म की अलर्ट लिस्ट में है।"),
    "CAMPAIGN_MEMORY_HIT": ("{indicator} belongs to a scam campaign already confirmed by bank analysts on {confirmed_on} ({campaign}).",
                            "{indicator} उस धोखाधड़ी अभियान का हिस्सा है जिसकी बैंक विश्लेषकों ने {confirmed_on} को पुष्टि की ({campaign})।"),
    # ---- Lane B · CHANGED ----------------------------------------------------
    "SOURCE_MISMATCH": ("This video re-uses footage from the genuine {source_title}, but the words were changed.",
                        "यह वीडियो असली {source_title_hi} की फ़ुटेज इस्तेमाल करता है, लेकिन शब्द बदल दिए गए हैं।"),
    "SOURCE_MATCH_SAME_WORDS": ("This video matches the genuine {source_title}, and the words are the same.",
                                "यह वीडियो असली {source_title_hi} से मेल खाता है, और शब्द भी वही हैं।"),
    "SOURCE_PARTIAL_EDIT": ("This video matches the genuine {source_title}, but some words differ from the original.",
                            "यह वीडियो असली {source_title} से मेल खाता है, लेकिन कुछ शब्द मूल से अलग हैं।"),
    "SOURCE_FOUND_NO_AUDIO_CHECK": ("This video re-uses footage from the genuine {source_title}; its audio could not be compared.",
                                    "यह वीडियो असली {source_title} की फ़ुटेज इस्तेमाल करता है; इसकी आवाज़ की तुलना नहीं हो सकी।"),
    "DETECTOR_INDICATOR": ("An automated {kind} deepfake detector flagged possible manipulation. Detectors make mistakes, so on its own this is only a hint.",
                           "एक ऑटोमैटिक {kind} डीपफ़ेक डिटेक्टर ने बदलाव का संकेत दिया। डिटेक्टर ग़लती करते हैं, इसलिए अकेले यह सिर्फ़ एक संकेत है।"),
    "DOC_SOURCE_MISMATCH": ("The notice cites {ref}, but that is a genuine {issuer} document about something else: \"{real_title}\".",
                            "नोटिस में {ref} लिखा है, लेकिन यह {issuer} का असली दस्तावेज़ किसी और विषय पर है: \"{real_title}\"।"),
    "DOC_REFERENCE_NOT_FOUND": ("We could not find the reference {ref} among {issuer}'s published documents.",
                                "{issuer} के प्रकाशित दस्तावेज़ों में {ref} नहीं मिला।"),
    "DOC_METADATA_EDITED": ("The file's metadata shows it was made with {tool}, a consumer editing tool.",
                            "फ़ाइल की जानकारी बताती है कि इसे {tool} से बनाया गया है, किसी आधिकारिक सिस्टम से नहीं।"),
    "DOC_SOURCE_MATCH": ("The notice's reference {ref} matches the genuine {issuer} circular \"{real_title}\".",
                         "नोटिस का संदर्भ {ref} {issuer} के असली सर्कुलर \"{real_title}\" से मेल खाता है।"),
    "DOC_REFERENCE_IMPOSSIBLE": ("The notice cites {ref}, but {why_en}.", "नोटिस में {ref} लिखा है, लेकिन {why_hi}।"),
    "DOC_ACTIVE_CONTENT": ("This PDF contains hidden active content ({features}). Genuine notices never need this; do not open it on a computer.",
                           "इस PDF में छिपा हुआ एक्टिव कंटेंट ({features}) है। असली नोटिस में इसकी ज़रूरत नहीं होती; इसे कंप्यूटर पर न खोलें।"),
    "DOC_INCREMENTAL_EDIT": ("The file was edited after it was first created ({saves} later save(s)).", "फ़ाइल बनने के बाद उसमें बदलाव किए गए ({saves} बार)।"),
    "C2PA_VALID": ("This file carries a valid digital signature (Content Credentials) from {signer}.",
                   "इस फ़ाइल पर {signer} का मान्य डिजिटल हस्ताक्षर (Content Credentials) है।"),
    "C2PA_INVALID": ("This file claims a digital signature, but it does not verify. It may have been altered.",
                     "इस फ़ाइल पर डिजिटल हस्ताक्षर का दावा है, पर वह सही नहीं निकला। फ़ाइल बदली गई हो सकती है।"),
    # ---- Lane C · WHAT -------------------------------------------------------
    "ASK_CREDENTIALS": ("It asks for an OTP, PIN or password. No bank, regulator or app ever asks for these.",
                        "यह OTP, PIN या पासवर्ड माँगता है। कोई बैंक, नियामक या ऐप कभी ये नहीं माँगता।"),
    "ASK_REMOTE_ACCESS": ("It asks you to install a screen-sharing app, which gives someone control of your phone.",
                          "यह स्क्रीन-शेयरिंग ऐप इंस्टॉल करवाता है, जिससे कोई और आपका फ़ोन चला सकता है।"),
    "ASK_SIDELOAD_APP": ("It asks you to install an app file ({apps}) from outside the official app store.",
                         "यह आधिकारिक ऐप स्टोर के बाहर से ऐप फ़ाइल ({apps}) इंस्टॉल करवाता है।"),
    "ASK_PAY_WITH_PRESSURE": ("It asks for money while pushing {pressure_en}.",
                              "यह {pressure_hi} के सहारे दबाव डालकर पैसे माँगता है।"),
    "PLAYBOOK_MATCH": ("It follows the pattern of a known scam: {name_en}.",
                       "यह एक जानी-मानी धोखाधड़ी जैसा है: {name_hi}।"),
    "PRESSURE_GUARANTEED_RETURNS": ("It promises guaranteed returns. SEBI-registered advisers cannot promise guaranteed returns.",
                                    "यह गारंटीड रिटर्न का वादा करता है। SEBI-रजिस्टर्ड सलाहकार गारंटीड रिटर्न का वादा नहीं कर सकते।"),
    "PRESSURE_URGENCY": ("It pushes you to act immediately.", "यह आपको तुरंत कुछ करने के लिए दबाव डालता है।"),
    "PRESSURE_SECRECY": ("It asks you to keep this secret from others.", "यह आपसे बात छिपाकर रखने को कहता है।"),
    "PRESSURE_THREAT": ("It threatens arrest, a block or legal action.", "यह गिरफ़्तारी, ब्लॉक या कानूनी कार्रवाई की धमकी देता है।"),
    "PRESSURE_AUTHORITY": ("It leans on official-sounding authority.", "यह किसी सरकारी पद या अधिकार का हवाला देता है।"),
    "PRESSURE_TOO_GOOD": ("It offers a prize or reward that sounds too good to be true.", "यह ऐसा इनाम देता है जो सच होने के लिए बहुत अच्छा लगता है।"),
    "ASK_PRIVATE_GROUP": ("It invites you into a private group or channel.", "यह आपको किसी प्राइवेट ग्रुप या चैनल में बुलाता है।"),
    "HIDDEN_INSTRUCTIONS": ("It contains hidden instructions aimed at automated checkers, a known manipulation trick.",
                            "इसमें ऑटोमैटिक जाँच को भटकाने वाले छिपे निर्देश हैं, जो एक जानी-मानी चाल है।"),
}

_FALLBACK = ("A check on this item did not pass ({code}).", "इस पर एक जाँच सफल नहीं हुई ({code})।")


def _fmt(tpl: str, params: dict) -> str:
    safe = defaultdict(lambda: "?", {k: (", ".join(map(str, v)) if isinstance(v, (list, tuple)) else v) for k, v in params.items()})
    return tpl.format_map(safe)


def reason_for(sig: Signal) -> Reason:
    code = sig.code
    en, hi = T.get(code) or T.get(code.replace("EMAIL_DOMAIN", "DOMAIN")) or _FALLBACK
    params = {"code": code, **sig.params}
    return Reason(code=code, lane=sig.lane, strength=sig.strength, text_en=_fmt(en, params), text_hi=_fmt(hi, params))
