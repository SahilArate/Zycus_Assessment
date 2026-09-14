"""Master-data matching, built to answer the brief's own warning: these sample
files have a handful of rows, real tenants have hundreds of thousands to millions.
So every lookup here is an indexed dict/set lookup, never a linear scan — and the
indexes are built once per run, not once per document.

Every resolver returns "" (empty) rather than guessing when nothing matches —
a fabricated code is explicitly called out in the brief as worse than a blank.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz, process

_NAME_FUZZY_THRESHOLD = 87  # conservative: prefer an honest blank over a bad guess


_STOPWORDS = {"office", "street", "road", "ltd", "building", "avenue", "floor", "city", "the", "and"}


def _address_keywords(address: str) -> set[str]:
    """Distinctive words from an address (city/country names mainly) — used to
    narrow down a business unit by location when the buyer's trading name on the
    document doesn't match its internal legal entity name."""
    tokens = re.split(r"[,\s]+", (address or "").lower())
    return {t.strip(".-") for t in tokens if len(t.strip(".-")) >= 4 and not t.isdigit() and t not in _STOPWORDS}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


@dataclass
class MasterData:
    suppliers: list[dict] = field(default_factory=list)
    companies: list[dict] = field(default_factory=list)
    taxes: list[dict] = field(default_factory=list)
    payment_terms: list[dict] = field(default_factory=list)
    purchase_orders: list[dict] = field(default_factory=list)

    # indexes, built once in __post_init__
    _supplier_by_vat: dict = field(default_factory=dict, repr=False)
    _supplier_by_iban: dict = field(default_factory=dict, repr=False)
    _supplier_by_email: dict = field(default_factory=dict, repr=False)
    _supplier_names_norm: dict = field(default_factory=dict, repr=False)  # norm_name -> supplier dict
    _po_by_number: dict = field(default_factory=dict, repr=False)
    _payment_term_alias: dict = field(default_factory=dict, repr=False)  # norm_alias -> term_id
    _business_units: list = field(default_factory=list, repr=False)  # flattened, one dict per BU
    _bu_names_norm: dict = field(default_factory=dict, repr=False)  # norm_name -> business unit dict

    def __post_init__(self):
        for s in self.suppliers:
            if s.get("vat_id"):
                self._supplier_by_vat[_norm(s["vat_id"])] = s
            if s.get("bank_iban"):
                self._supplier_by_iban[_norm(s["bank_iban"])] = s
            if s.get("email"):
                self._supplier_by_email[s["email"].strip().lower()] = s
            if s.get("name"):
                self._supplier_names_norm[_norm(s["name"])] = s

        for po in self.purchase_orders:
            if po.get("po_number"):
                self._po_by_number[_norm(po["po_number"])] = po

        for term in self.payment_terms:
            for alias in term.get("text_aliases", []):
                self._payment_term_alias[_norm(alias)] = term["payment_term_id"]

        for company in self.companies:
            for bu in company.get("business_units", []):
                for loc in bu.get("locations", []):
                    entry = {
                        "company_code": company["company_code"],
                        "business_unit_code": bu["business_unit_code"],
                        "business_unit_name": bu.get("business_unit_name", ""),
                        "location_code": loc["location_code"],
                        "address_keywords": _address_keywords(loc.get("invoice_to_address", "")),
                    }
                    self._business_units.append(entry)
                    if entry["business_unit_name"]:
                        self._bu_names_norm[_norm(entry["business_unit_name"])] = entry

    @classmethod
    def load(cls, master_data_dir: str | Path) -> "MasterData":
        d = Path(master_data_dir)
        suppliers = json.loads((d / "suppliers.json").read_text(encoding="utf-8"))["suppliers"]
        companies = json.loads((d / "chart_of_books.json").read_text(encoding="utf-8"))["companies"]
        taxes = json.loads((d / "tax_master.json").read_text(encoding="utf-8"))["taxes"]
        payment_terms = json.loads((d / "payment_terms.json").read_text(encoding="utf-8"))["payment_terms"]
        purchase_orders = json.loads((d / "po_master.json").read_text(encoding="utf-8"))["purchase_orders"]
        return cls(suppliers, companies, taxes, payment_terms, purchase_orders)

    # ---- resolvers -------------------------------------------------------

    def resolve_supplier(self, *, name: str = "", vat_id: str = "", iban: str = "", email: str = "") -> tuple[str, str]:
        """Returns (supplier_id, matched_field) or ("", "") if no confident match.
        Priority: VAT id > IBAN > email > fuzzy name (conservative threshold)."""
        if vat_id and (hit := self._supplier_by_vat.get(_norm(vat_id))):
            return hit["supplier_id"], "vat_id"
        if iban and (hit := self._supplier_by_iban.get(_norm(iban))):
            return hit["supplier_id"], "iban"
        if email and (hit := self._supplier_by_email.get(email.strip().lower())):
            return hit["supplier_id"], "email"
        if name:
            norm = _norm(name)
            if hit := self._supplier_names_norm.get(norm):
                return hit["supplier_id"], "name_exact"
            candidates = list(self._supplier_names_norm.keys())
            if candidates:
                best = process.extractOne(norm, candidates, scorer=fuzz.WRatio)
                if best and best[1] >= _NAME_FUZZY_THRESHOLD:
                    return self._supplier_names_norm[best[0]]["supplier_id"], "name_fuzzy"
        return "", ""

    def resolve_tax(self, *, country: str = "", tax_type: str = "", rate_percent: str = "") -> str:
        """Match on (country, tax_type, rate) — the combination that's actually unique
        in a real tax master; rate alone collides across countries and tax types."""
        try:
            rate = float(str(rate_percent).replace("%", "").strip())
        except ValueError:
            rate = None
        for t in self.taxes:
            country_ok = (not country) or t.get("country", "").upper() == country.upper()
            type_ok = (not tax_type) or t.get("tax_type", "").upper() == tax_type.upper()
            rate_ok = rate is None or abs(float(t.get("rate", -999)) - rate) < 0.01
            if country_ok and type_ok and rate_ok:
                return t["code"]
        return ""

    def resolve_payment_term(self, text: str) -> str:
        if not text:
            return ""
        norm = _norm(text)
        if hit := self._payment_term_alias.get(norm):
            return hit
        candidates = list(self._payment_term_alias.keys())
        if candidates:
            best = process.extractOne(norm, candidates, scorer=fuzz.partial_ratio)
            if best and best[1] >= 90:
                return self._payment_term_alias[best[0]]
        return ""

    def resolve_po(self, po_number: str) -> str:
        if not po_number:
            return ""
        hit = self._po_by_number.get(_norm(po_number))
        return hit["po_id"] if hit else ""

    def resolve_buyer(self, *, buyer_name: str = "", buyer_address: str = "") -> dict:
        """Matches the buyer PRINTED ON THE DOCUMENT against our own business
        units — chart_of_books.json's own comment says this varies per document,
        it is not fixed for a run. Priority:
          1. Exact/fuzzy match on the business unit's legal name.
          2. Otherwise, address keywords (city/country) — but ONLY if they point
             to exactly one business unit. Estonia has two entities at the same
             address (Bolt Technology OU vs Bolt Holdings OU); address alone
             can't tell them apart, so an ambiguous address match stays blank
             rather than guessing between them (Rule 2: no match is legitimate).
        Returns {"company_code","business_unit_code","location_code"}, all ""
        if nothing resolves confidently."""
        blank = {"company_code": "", "business_unit_code": "", "location_code": ""}

        if buyer_name:
            norm = _norm(buyer_name)
            if hit := self._bu_names_norm.get(norm):
                return {k: hit[k] for k in ("company_code", "business_unit_code", "location_code")}
            candidates = list(self._bu_names_norm.keys())
            if candidates:
                best = process.extractOne(norm, candidates, scorer=fuzz.WRatio)
                if best and best[1] >= _NAME_FUZZY_THRESHOLD:
                    hit = self._bu_names_norm[best[0]]
                    return {k: hit[k] for k in ("company_code", "business_unit_code", "location_code")}

        if buyer_address:
            addr_keywords = _address_keywords(buyer_address)
            matches = [bu for bu in self._business_units if addr_keywords & bu["address_keywords"]]
            if len(matches) == 1:
                hit = matches[0]
                return {k: hit[k] for k in ("company_code", "business_unit_code", "location_code")}
            # 0 or >1 matches (ambiguous, e.g. two entities at the same city) — stay blank

        return blank