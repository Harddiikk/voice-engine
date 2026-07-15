"""normalize_phone_number: +91 handling for the formats Indian sheets contain."""

from api.services.campaign.source_sync import CampaignSourceSyncService

norm = CampaignSourceSyncService.normalize_phone_number


class TestIndianFormats:
    def test_bare_10_digit_gets_cc(self):
        assert norm("9876543210", "+91") == "+919876543210"

    def test_trunk_zero_stripped(self):
        assert norm("09876543210", "+91") == "+919876543210"

    def test_cc_without_plus_not_double_prefixed(self):
        assert norm("919876543210", "+91") == "+919876543210"

    def test_international_00_prefix(self):
        assert norm("00919876543210", "+91") == "+919876543210"

    def test_already_e164_untouched(self):
        assert norm("+919876543210", "+91") == "+919876543210"

    def test_spaces_dashes_parens_stripped(self):
        assert norm("98765 43210", "+91") == "+919876543210"
        assert norm("98765-43210", "+91") == "+919876543210"
        assert norm("(91) 98765 43210", "+91") == "+919876543210"

    def test_cc_given_without_plus(self):
        assert norm("9876543210", "91") == "+919876543210"

    def test_all_variants_collapse_to_same_number(self):
        variants = [
            "9876543210",
            "09876543210",
            "919876543210",
            "00919876543210",
            "+919876543210",
            "98765 43210",
        ]
        assert {norm(v, "+91") for v in variants} == {"+919876543210"}


class TestEdgeCases:
    def test_empty_passthrough(self):
        assert norm("", "+91") == ""
        assert norm(None, "+91") == ""

    def test_no_country_code_leaves_digits(self):
        assert norm("9876543210", None) == "9876543210"

    def test_91_start_but_10_digits_is_national_number(self):
        # 10-digit number that happens to start with 91 — NOT a country code.
        assert norm("9198765432", "+91") == "+919198765432"

    def test_other_country_code(self):
        assert norm("14155552671", "+1") == "+14155552671"
        assert norm("4155552671", "+1") == "+14155552671"
