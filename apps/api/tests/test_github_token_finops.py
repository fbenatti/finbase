import pandas as pd

from app.services.github_token_extractor import GithubTokenExtractor


def test_billing_rows_keep_credits_separate_from_interactions():
    frame = pd.DataFrame([
        {
            "date": "2026-08-20",
            "username": "81075269_tefbr",
            "unit_type": "ai-credits",
            "quantity": "5534.4",
            "gross_amount": "55.34",
            "discount_amount": "0",
            "net_amount": "55.34",
            "sku": "AI Credit GAC",
        }
    ])

    result = GithubTokenExtractor._normalize_billing_csv(frame).iloc[0]

    assert result["source_kind"] == "billing_report"
    assert result["ai_credits"] == "5534.4"
    assert result["total_chat_turns"] == ""
    assert result["gross_amount"] == "55.34"
    assert result["net_amount"] == "55.34"


def test_metrics_rows_are_marked_as_adoption_source():
    records = GithubTokenExtractor._canonical_from_usage_row({
        "day": "2026-08-20",
        "user_login": "81075269_tefbr",
        "totals_by_language_model": [{
            "model": "claude-sonnet-5",
            "feature": "chatagent",
            "loc_added_sum": 5,
            "user_initiated_interaction_count": 1,
        }],
    })

    assert records[0]["source_kind"] == "copilot_metrics"
    assert records[0]["total_lines_accepted"] == 5
    assert records[0]["ai_credits"] == ""


def test_delayed_current_report_uses_latest_completed_monthly_export(monkeypatch):
    extractor = GithubTokenExtractor.__new__(GithubTokenExtractor)
    monkeypatch.setattr(
        extractor, "_fetch_copilot_metrics_for_period",
        lambda period: pd.DataFrame([{"day": "2026-09-22", "source_kind": "copilot_metrics"}]),
    )

    def unavailable(period):
        raise RuntimeError("relatório do dia ainda indisponível")

    monkeypatch.setattr(extractor, "_fetch_billing_report_for_period", unavailable)
    monkeypatch.setattr(
        extractor, "_latest_completed_billing_report",
        lambda period: (
            pd.DataFrame([{"day": "2026-09-25", "source_kind": "ai_usage_report", "ai_credits": "10"}]),
            "completed-report",
        ),
    )

    result = extractor._fetch_tokens_for_period("202609")

    assert set(result["source_kind"]) == {"copilot_metrics", "ai_usage_report"}
    assert result.loc[result["source_kind"] == "ai_usage_report", "ai_credits"].iloc[0] == "10"
