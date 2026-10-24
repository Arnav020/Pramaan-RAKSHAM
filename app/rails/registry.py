"""Loads the trust-rail datasets once, keeping each dataset's provenance."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache

from app.config import RAILS_DATA_DIR
from app.models import Provenance


def _load(name: str) -> dict:
    return json.loads((RAILS_DATA_DIR / name).read_text(encoding="utf-8"))


def _prov(d: dict) -> Provenance:
    p = d.get("_provenance", {})
    return Provenance(dataset=p.get("dataset", "?"), source_url=p.get("source_url"), fetched_at=p.get("fetched_at"), note=p.get("note"))


@dataclass
class Entity:
    id: str
    name: str
    kind: str                     # bank | regulator
    aliases: list[str]
    domains: list[str]
    sms_headers: list[str] = field(default_factory=list)
    collects_money_from_public: bool = True


@dataclass
class Registry:
    banks: list[Entity]
    regulators: list[Entity]
    psp_handles: set[str]
    valid_prefix: str
    sebi_categories: dict[str, str]
    alert_list: list[dict]
    fri: dict[str, str]
    sebi_snapshot: dict[str, dict]
    sebi_counts: dict[str, int]
    prov: dict[str, Provenance]

    # -- lookups ---------------------------------------------------------
    def all_entities(self) -> list[Entity]:
        return self.banks + self.regulators

    def entity_for_domain(self, reg_domain: str) -> Entity | None:
        for e in self.all_entities():
            if reg_domain in e.domains:
                return e
        return None

    def official_domains(self) -> set[str]:
        return {d for e in self.all_entities() for d in e.domains}

    def find_entity_by_name(self, text: str) -> Entity | None:
        t = f" {text.lower()} "
        best: tuple[int, Entity | None] = (0, None)
        for e in self.all_entities():
            for a in e.aliases + [e.name.lower()]:
                a_ = a.lower()
                if len(a_.strip()) <= 5:
                    # short aliases must be whole words: 'validhdfc' is not an HDFC claim
                    hit = re.search(rf"(?<![\w]){re.escape(a_.strip())}(?![\w])", t) is not None
                else:
                    hit = a_ in t
                if hit and len(a_) > best[0]:
                    best = (len(a_), e)
        return best[1]

    def bank_for_header(self, header_core: str) -> Entity | None:
        for b in self.banks:
            if header_core.upper() in b.sms_headers:
                return b
        return None

    def alert_list_hit(self, text: str) -> dict | None:
        """Whole-word name match (so 'Trive' never fires on 'contrive') or a listed website."""
        low = text.lower()
        for ent in self.alert_list:
            for n in [ent["name"]] + ent.get("aliases", []):
                n_low = n.lower().strip()
                if len(re.sub(r"[^a-z0-9]", "", n_low)) < 3:
                    continue
                if re.search(rf"(?<![a-z0-9]){re.escape(n_low)}(?![a-z0-9])", low):
                    return ent
                # multi-word names written together, e.g. 'StarnetFX' for 'Starnet FX'
                compact = re.sub(r"[^a-z0-9]", "", n_low)
                if " " in n_low and len(compact) >= 6 and re.search(rf"(?<![a-z0-9]){compact}(?![a-z0-9])", low):
                    return ent
            for d in ent.get("domains", []):
                if re.search(rf"(?<![a-z0-9.\-]){re.escape(d)}(?![a-z0-9\-])", low):
                    return ent
        return None


@lru_cache(maxsize=1)
def get_registry() -> Registry:
    banks_d, regs_d, upi_d = _load("banks.json"), _load("regulators.json"), _load("upi.json")
    alert_d, fri_d, sebi_d = _load("alert_list.json"), _load("fri_mock.json"), _load("sebi_register_snapshot.json")
    banks = [Entity(id=b["id"], name=b["name"], kind="bank", aliases=b["aliases"], domains=b["domains"], sms_headers=b.get("sms_headers", [])) for b in banks_d["banks"]]
    regs = [Entity(id=r["id"], name=r["name"], kind="regulator", aliases=r["aliases"], domains=r["domains"], collects_money_from_public=r.get("collects_money_from_public", False)) for r in regs_d["regulators"]]
    return Registry(
        banks=banks,
        regulators=regs,
        psp_handles=set(upi_d["consumer_psp_handles"]),
        valid_prefix=upi_d["valid_handle_prefix"],
        sebi_categories=upi_d["sebi_categories"],
        alert_list=alert_d["entities"],
        fri=fri_d["numbers"],
        sebi_snapshot=sebi_d["entries"],
        sebi_counts=sebi_d["counts"],
        prov={
            "banks": _prov(banks_d), "regulators": _prov(regs_d), "upi": _prov(upi_d),
            "alert_list": _prov(alert_d), "fri": _prov(fri_d), "sebi": _prov(sebi_d),
            "trai_1600": Provenance(
                dataset="TRAI direction: 1600-series for BFSI service/transaction calls",
                source_url="https://ianslive.in/trai-orders-mandatory-adoption-of-1600-series-numbers-by-banks-financial-services-insurance-firms--20251119152705",
                fetched_at="2026-10-22",
                note="Deadlines Jan-Mar 2026 (banks 1 Jan, NBFCs 1 Feb-1 Mar, MFs 15 Feb, brokers 15 Mar, insurers 15 Feb). 140-series is for promotional calls.",
            ),
            "dlt": Provenance(dataset="TRAI DLT SMS header suffixes", note="-P promotional, -S service, -T transactional, -G government (from 6 May 2025)."),
        },
    )
