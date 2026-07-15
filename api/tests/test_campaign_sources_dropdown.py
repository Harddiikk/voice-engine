"""Sheet-reuse dropdown: repeated uploads of one filename collapse to the latest."""

from datetime import UTC, datetime, timedelta

from api.routes.campaign import dedupe_sources_by_filename


def _src(uuid, name, ts, count=1, rows=1):
    return {
        "source_id": f"campaigns/26/{uuid}_{name}",
        "total_rows": rows,
        "first_used_at": ts,
        "last_used_at": ts,
        "campaigns_count": count,
    }


def test_same_filename_collapses_to_latest_upload():
    t0 = datetime(2026, 7, 15, tzinfo=UTC)
    sources = [  # newest first, as the DB query returns them
        _src("cccc", "sample.csv", t0),
        _src("bbbb", "sample.csv", t0 - timedelta(days=1)),
        _src("aaaa", "sample.csv", t0 - timedelta(days=2), count=2),
        _src("dddd", "leads.csv", t0 - timedelta(days=3), rows=2000),
    ]
    out = dedupe_sources_by_filename(sources)
    assert [s["filename"] for s in out] == ["sample.csv", "leads.csv"]
    assert out[0]["source_id"] == "campaigns/26/cccc_sample.csv"  # latest upload wins
    assert out[0]["campaigns_count"] == 4  # aggregated across uploads
    assert out[0]["first_used_at"] == t0 - timedelta(days=2)
    assert out[1]["total_rows"] == 2000


def test_cap_at_dropdown_max():
    t0 = datetime(2026, 7, 15, tzinfo=UTC)
    sources = [_src(f"u{i}", f"file{i}.csv", t0 - timedelta(hours=i)) for i in range(30)]
    out = dedupe_sources_by_filename(sources)
    assert len(out) == 15
    assert out[0]["filename"] == "file0.csv"  # newest kept first
