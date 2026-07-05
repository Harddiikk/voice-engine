"""Per-campaign spend cap: campaign_budget_exhausted."""

from api.services.campaign.budget import campaign_budget_exhausted


def test_no_budget_never_exhausted():
    assert campaign_budget_exhausted(None) is False
    assert campaign_budget_exhausted({}) is False
    assert campaign_budget_exhausted({"consumed_seconds": 99999}) is False


def test_under_budget():
    assert campaign_budget_exhausted(
        {"budget_seconds": 6000, "consumed_seconds": 5999}
    ) is False


def test_at_or_over_budget():
    assert campaign_budget_exhausted(
        {"budget_seconds": 6000, "consumed_seconds": 6000}
    ) is True
    assert campaign_budget_exhausted(
        {"budget_seconds": 6000, "consumed_seconds": 7200}
    ) is True


def test_missing_consumed_defaults_zero():
    assert campaign_budget_exhausted({"budget_seconds": 60}) is False
