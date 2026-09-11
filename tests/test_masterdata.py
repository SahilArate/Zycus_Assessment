import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bookable_payable.masterdata import MasterData

MD = MasterData.load(Path(__file__).resolve().parents[1] / "master_data")


def test_supplier_matches_by_vat_id():
    # exact VAT id from sample_autodraft.json / Phocus GmbH
    sid, field = MD.resolve_supplier(vat_id="DE209177122")
    assert sid == "2845695"
    assert field == "vat_id"


def test_supplier_no_match_is_honest_blank():
    sid, field = MD.resolve_supplier(name="Totally Unknown Supplier LLC", vat_id="XX000")
    assert sid == ""
    assert field == ""


def test_supplier_fuzzy_name_still_conservative():
    # a near-identical name should match
    sid, _ = MD.resolve_supplier(name="Phocus Direct Communication Gmbh")  # case differs
    assert sid == "2845695"


def test_supplier_fuzzy_name_does_not_overmatch_different_company():
    # should NOT match a totally different supplier just because both are German GmbHs
    sid, _ = MD.resolve_supplier(name="Some Other Random Company Gmbh")
    assert sid == ""


def test_tax_resolves_german_reduced_vat():
    code = MD.resolve_tax(country="DE", tax_type="VAT", rate_percent="7")
    assert code == "DE_070_VAT"


def test_tax_resolves_ghana_nhil_not_confused_with_vat():
    code = MD.resolve_tax(country="GH", tax_type="NHIL", rate_percent="2.5")
    assert code == "TAX028"


def test_tax_no_match_when_rate_absent_from_master():
    code = MD.resolve_tax(country="DE", tax_type="VAT", rate_percent="99")
    assert code == ""


def test_payment_term_alias_net_10():
    assert MD.resolve_payment_term("Net 10") == "Net_10"


def test_payment_term_thai_90_days_without_deduction():
    # from HLD-01: "Up to 28.09.2026 without deduction" / "90 days net" style text
    assert MD.resolve_payment_term("without deduction 90") == "Net_90"


def test_payment_term_unmatched_is_blank():
    assert MD.resolve_payment_term("net 45 unusual terms") == ""


def test_po_resolves_known_po():
    assert MD.resolve_po("PO-EE-2026-0044") == "PO-EE-2026-0044"


def test_po_unknown_number_is_blank():
    assert MD.resolve_po("PO-NOT-IN-MASTER-9999") == ""


def test_buyer_resolves_known_company_and_bu():
    buyer = MD.resolve_buyer("BOLTGROUP", "EE004")
    assert buyer["location_code"] == "LOC_EE_001"


def test_buyer_unknown_bu_is_blank():
    buyer = MD.resolve_buyer("BOLTGROUP", "NOT_REAL")
    assert buyer["location_code"] == ""
    assert buyer["company_code"] == ""
