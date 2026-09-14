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


def test_tax_index_is_bucketed_by_type_not_a_flat_list():
    # proves this is a real index, not just a renamed full scan: every row in the
    # VAT bucket must actually be a VAT row, and the bucket must exist and be non-empty
    assert "VAT" in MD._tax_by_type
    assert MD._tax_by_type["VAT"]
    assert all(t.get("tax_type", "").upper() == "VAT" for t in MD._tax_by_type["VAT"])


def test_tax_unknown_type_is_honest_blank_not_an_index_crash():
    code = MD.resolve_tax(country="DE", tax_type="NOT_A_REAL_TAX_TYPE", rate_percent="7")
    assert code == ""


def test_tax_resolves_without_country_using_type_and_rate_only():
    # country="" must still work as a wildcard through the new index path
    code = MD.resolve_tax(tax_type="VAT", rate_percent="7")
    assert code == "DE_070_VAT"


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


def test_buyer_resolves_by_exact_business_unit_name():
    buyer = MD.resolve_buyer(buyer_name="Bolt Ghana Ltd")
    assert buyer["business_unit_code"] == "GH001"
    assert buyer["location_code"] == "LOC_GH_001"


def test_buyer_resolves_by_address_when_only_one_bu_at_that_location():
    # no usable name given, but the address alone points to exactly one BU
    buyer = MD.resolve_buyer(buyer_address="Jalan Tun Razak, 50400 Kuala Lumpur, Malaysia")
    assert buyer["business_unit_code"] == "MY001"


def test_buyer_estonia_ambiguity_stays_honestly_blank():
    # Bolt Technology OU (EE001) and Bolt Holdings OU (EE004) share the same
    # Tallinn address — address alone can't tell them apart, and an unrelated
    # trading name gives no name match either. Guessing between two real
    # candidates is not a "real match" (Rule 2) — must stay blank.
    buyer = MD.resolve_buyer(buyer_name="Northwind Operations OU", buyer_address="Vana-Louna 15, 10134 Tallinn, Estonia")
    assert buyer["business_unit_code"] == ""
    assert buyer["company_code"] == ""


def test_buyer_completely_unrelated_entity_is_blank():
    buyer = MD.resolve_buyer(buyer_name="Random Unrelated Company", buyer_address="123 Nowhere St, Nowhereville")
    assert buyer["company_code"] == ""
    assert buyer["business_unit_code"] == ""
    assert buyer["location_code"] == ""