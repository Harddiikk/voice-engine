"""Client segmentation tag normalisation.

Tags exist so the owner can slice the client list ("via-shreyas", "gym",
"pilot"). The failure mode that matters is silent: two spellings of the same
label splitting one segment into two, so a filter quietly under-reports.
"""

from api.services.admin.profile import MAX_TAGS, MAX_TAG_LENGTH, normalize_tags


class TestNormalizeTags:
    def test_empty_inputs(self):
        assert normalize_tags(None) == []
        assert normalize_tags([]) == []

    def test_lowercases_so_case_variants_are_one_segment(self):
        assert normalize_tags(["Gym", "GYM", "gym"]) == ["gym"]

    def test_collapses_internal_and_outer_whitespace(self):
        # "Via  Shreyas" and "via shreyas" must not become two segments.
        assert normalize_tags(["Via  Shreyas", " via shreyas "]) == ["via shreyas"]

    def test_preserves_first_seen_order(self):
        # The list UI shows the first tag when space is tight, so order matters.
        assert normalize_tags(["pilot", "gym", "enterprise"]) == [
            "pilot",
            "gym",
            "enterprise",
        ]

    def test_drops_non_strings_without_exploding(self):
        # Defensive: the payload is caller-supplied JSON.
        assert normalize_tags(["gym", None, 42, {"a": 1}, "pilot"]) == ["gym", "pilot"]

    def test_drops_blank_and_whitespace_only(self):
        assert normalize_tags(["", "   ", "gym"]) == ["gym"]

    def test_truncates_overlong_tag(self):
        long_tag = "x" * (MAX_TAG_LENGTH + 50)
        result = normalize_tags([long_tag])
        assert len(result) == 1
        assert len(result[0]) == MAX_TAG_LENGTH

    def test_caps_tag_count(self):
        result = normalize_tags([f"tag{i}" for i in range(MAX_TAGS + 25)])
        assert len(result) == MAX_TAGS

    def test_dedupes_after_truncation_not_before(self):
        # Two long tags identical up to the cap collapse into one segment
        # rather than becoming visually identical duplicates in the UI.
        a = "y" * MAX_TAG_LENGTH + "-alpha"
        b = "y" * MAX_TAG_LENGTH + "-beta"
        assert normalize_tags([a, b]) == ["y" * MAX_TAG_LENGTH]
