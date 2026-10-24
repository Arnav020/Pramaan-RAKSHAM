"""Ready-to-paste complaint drafts for the Golden-Hour Kit: what to say on 1930, the NCRP
complaint text, and an email to the bank's fraud desk.

Filled from the check (payee, serial, reasons, proof link). The UTR, amount and time stay as
blanks the person fills in: Pramaan never asks for or stores transaction details.
A human always files the complaint; nothing is sent automatically.
"""

from __future__ import annotations

from datetime import timedelta, timezone

from app.models import ContactType

_IST = timezone(timedelta(hours=5, minutes=30))
BLANK = {"amount": "[amount, e.g. ₹10,000]", "when": "[date and time you paid]", "utr": "[UTR / transaction ID from your bank SMS]",
         "bank": "[your bank]", "name": "[your name]", "mobile": "[your mobile number]"}


def _facts(rec) -> dict:
    cps = rec.verdict.extraction.contact_points
    pick = lambda kind: [c.value for c in cps if c.type == kind]
    accounts = [v.replace("|", ", IFSC ") for v in pick(ContactType.BANK_ACCOUNT)]
    return {
        "payee": ", ".join(pick(ContactType.UPI) + accounts) or "[UPI ID or account you paid]",
        "phones": ", ".join(pick(ContactType.PHONE)),
        "links": ", ".join(pick(ContactType.URL)),
        "serial": rec.serial,
        "checked": rec.verdict.created_at.astimezone(_IST).strftime("%d %b %Y, %H:%M IST"),
        "verdict": rec.verdict.title_en,
        "reasons": [r.text_en for r in rec.verdict.reasons[:3]],
        "proof": rec.proof_url,
        "verify": rec.verify_url,
    }


def call_script(rec, lang: str = "en") -> str:
    f = _facts(rec)
    if lang == "hi":
        return (f"नमस्ते, मेरे साथ ऑनलाइन धोखाधड़ी हुई है। मैंने {BLANK['when']} पर {BLANK['amount']} भेजे, "
                f"पाने वाला: {f['payee']}। UTR: {BLANK['utr']}। मेरा बैंक: {BLANK['bank']}। "
                f"कृपया यह रकम रोकने (फ़्रीज़ करने) में मदद करें। मेरे पास सबूत है: प्रमाण प्रमाणपत्र {f['serial']}।")
    return (f"Hello, I want to report an online financial fraud. I paid {BLANK['amount']} on {BLANK['when']} "
            f"to {f['payee']}. The UTR is {BLANK['utr']}. My bank is {BLANK['bank']}. "
            f"Please help freeze this amount. I have evidence: Pramaan certificate {f['serial']}.")


def ncrp_text(rec) -> str:
    f = _facts(rec)
    lines = [
        "Category: Online Financial Fraud",
        "",
        f"I was targeted by a message that {('matched a known scam pattern' if rec.verdict.level.value == 'RED' else 'could not be verified')} "
        f"(Pramaan check {f['serial']}, {f['checked']}). I paid {BLANK['amount']} on {BLANK['when']}.",
        "",
        f"Paid to (beneficiary): {f['payee']}",
        f"Transaction ID / UTR: {BLANK['utr']}",
        f"My bank: {BLANK['bank']}",
    ]
    if f["phones"]:
        lines.append(f"Phone numbers used by the sender: {f['phones']}")
    if f["links"]:
        lines.append(f"Links in the message: {f['links']}")
    lines += ["", "Why it is suspicious (independent check):", *[f"- {r}" for r in f["reasons"]], "",
              f"Evidence page: {f['proof']}", f"Signed certificate (verify): {f['verify']}",
              "Attached: screenshots of the conversation and the payment confirmation."]
    return "\n".join(lines)


def bank_email(rec) -> tuple[str, str]:
    f = _facts(rec)
    subject = f"Fraud report: request to freeze {BLANK['amount']} sent to {f['payee']}"
    body = "\n".join([
        "Dear Fraud Risk Management team,",
        "",
        f"I made a payment that I now believe was fraudulent. Please block further debits if needed and request a freeze of the beneficiary.",
        "",
        f"Amount: {BLANK['amount']}",
        f"Date and time: {BLANK['when']}",
        f"UTR / transaction ID: {BLANK['utr']}",
        f"Beneficiary: {f['payee']}",
        "",
        "An independent Pramaan check of the message found:",
        *[f"- {r}" for r in f["reasons"]],
        f"Evidence: {f['proof']} (signed certificate {f['serial']}: {f['verify']})",
        "",
        "I have also reported this on 1930 / cybercrime.gov.in. Acknowledgement number: [NCRP acknowledgement number]",
        "",
        f"{BLANK['name']}, {BLANK['mobile']}",
    ])
    return subject, body


def full_packet(rec) -> str:
    """One text file with all three drafts, for the proof page download."""
    subject, body = bank_email(rec)
    return "\n".join([
        f"PRAMAAN GOLDEN-HOUR PACKET · certificate {rec.serial}",
        "Fill in the [blanks] yourself. Pramaan never asks for your UTR or bank details and files nothing for you.",
        "", "1. CALL 1930 NOW (national cybercrime helpline). Say:", "", call_script(rec), "",
        "2. FILE AT https://cybercrime.gov.in  (Report Cyber Crime → Financial Fraud). Paste:", "", ncrp_text(rec), "",
        "3. EMAIL OR CALL YOUR BANK (number printed on your card). Subject:", subject, "", body, "",
        "We cannot promise recovery, but reporting within the first hours gives the best chance of a freeze.",
    ])
