"""Core data types shared by every stage of the engine.

The flow is: Item -> Extraction -> three LaneResults (each a list of Signals)
-> Verdict. Every Signal carries a reason code, a lane, a strength and the
evidence behind it, so any verdict can be traced back to facts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Lane(str, Enum):
    A = "A"  # WHO: provenance against official trust rails
    B = "B"  # CHANGED: manipulation (Source-Match, weak forensics)
    C = "C"  # WHAT: intent (the ask, pressure, scam playbooks)


class Strength(str, Enum):
    STRONG = "STRONG"      # can count towards a red verdict
    WEAK = "WEAK"          # a hint; never decisive alone
    POSITIVE = "POSITIVE"  # evidence the sender is genuine
    INFO = "INFO"          # context only, no effect on the verdict


class ContactType(str, Enum):
    UPI = "upi"
    PHONE = "phone"
    URL = "url"
    EMAIL = "email"
    BANK_ACCOUNT = "bank_account"
    SMS_HEADER = "sms_header"
    SEBI_REG_NO = "sebi_reg_no"
    APP = "app"


class ClaimType(str, Enum):
    BANK = "bank"
    NBFC = "nbfc"
    SEBI_INTERMEDIARY = "sebi_intermediary"  # broker, adviser, research analyst, MF
    REGULATOR = "regulator"                  # RBI, SEBI, govt, police, court
    PUBLIC_FIGURE = "public_figure"
    PAYMENT_APP = "payment_app"
    NONE = "none"


class AskType(str, Enum):
    PAY = "pay"
    CREDENTIALS = "credentials"   # OTP, PIN, password, CVV
    INSTALL_APP = "install_app"
    PRIVATE_GROUP = "private_group"
    CLICK_LINK = "click_link"
    REMOTE_ACCESS = "remote_access"
    CALL_BACK = "call_back"
    NONE = "none"


class Pressure(str, Enum):
    URGENCY = "urgency"
    SECRECY = "secrecy"
    GUARANTEED_RETURNS = "guaranteed_returns"
    THREAT = "threat"
    AUTHORITY = "authority"
    TOO_GOOD = "too_good"


class LaneStatusA(str, Enum):
    VERIFIED = "VERIFIED"
    UNVERIFIABLE = "UNVERIFIABLE"
    CONTRADICTED = "CONTRADICTED"


class LaneStatusB(str, Enum):
    NONE = "NONE"
    INDICATORS = "INDICATORS"
    SOURCE_MISMATCH = "SOURCE_MISMATCH"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class AskRisk(str, Enum):
    NONE = "NONE"
    LOW = "LOW"
    HIGH = "HIGH"


class VerdictLevel(str, Enum):
    GREEN = "GREEN"   # "Verified sender"
    AMBER = "AMBER"   # "Can't verify"
    RED = "RED"       # "Matches a known scam pattern"


class Provenance(BaseModel):
    """Where a fact we rely on came from: our own data is cited too."""

    dataset: str
    source_url: str | None = None
    fetched_at: str | None = None
    note: str | None = None


class ContactPoint(BaseModel):
    type: ContactType
    value: str            # normalised value
    raw: str              # exactly as it appeared in the message
    context: str = ""     # a few words around it, for explanations


class Claim(BaseModel):
    type: ClaimType = ClaimType.NONE
    name: str | None = None   # e.g. "SBI", "SEBI-registered adviser", "[Public Figure]"
    evidence: str | None = None


class Extraction(BaseModel):
    """The fixed schema the extractor fills. It describes; it never decides."""

    text: str = ""
    language: str = "en"
    claims: list[Claim] = Field(default_factory=list)
    contact_points: list[ContactPoint] = Field(default_factory=list)
    asks: list[AskType] = Field(default_factory=list)
    pressure: list[Pressure] = Field(default_factory=list)
    hidden_instructions: bool = False
    extractor: str = "rules"   # which path produced it: rules, rules+groq, rules+gemini
    ai_saw: str | None = None                                  # the masked text sent to the AI (analyst trace; wiped after 24 h)
    ai_masked: dict[str, int] = Field(default_factory=dict)    # what was masked before the AI saw it, by kind
    injection_score: float | None = None                       # Prompt Guard 2: probability of instructions aimed at AI
    ai_script: dict | None = None    # {"id", "quote"}: the known scam script the AI says the wording follows (quote checked)


class Signal(BaseModel):
    code: str
    lane: Lane
    strength: Strength
    params: dict[str, Any] = Field(default_factory=dict)  # facts used by the explainer
    provenance: Provenance | None = None


class LaneResult(BaseModel):
    lane: Lane
    status: str
    signals: list[Signal] = Field(default_factory=list)
    available: bool = True        # False if the lane could not run (e.g. model down)
    detail: dict[str, Any] = Field(default_factory=dict)


class Reason(BaseModel):
    code: str
    lane: Lane
    strength: Strength
    text_en: str
    text_hi: str


class Verdict(BaseModel):
    level: VerdictLevel
    title_en: str
    title_hi: str
    reasons: list[Reason]          # at most three are shown to the user
    rule_id: str                   # which fusion rule fired
    rules_version: str
    strong_lanes: list[Lane]
    needs_analyst: bool = False    # e.g. verified sender + suspicious signals
    advice_en: str = ""            # the one next step shown under the verdict (depends on why it is amber)
    advice_hi: str = ""
    amber_kind: str | None = None  # high_risk · nothing_to_check · unverified (amber only)
    lanes: dict[str, LaneResult]
    extraction: Extraction
    created_at: datetime = Field(default_factory=utcnow)
