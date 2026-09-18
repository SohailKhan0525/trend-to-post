from pathlib import Path

import pytest

from app.x_trends import XTrendClient, XTrendError, _normalize_trends


def test_normalize_trends_deduplicates_and_limits():
    raw = [
        type("Trend", (), {"name": "AI"})(),
        type("Trend", (), {"name": "ai"})(),
        type("Trend", (), {"name": "OpenAI"})(),
        type("Trend", (), {"name": ""})(),
        type("Trend", (), {"name": "Robotics"})(),
    ]

    assert _normalize_trends(raw, 2) == ["AI", "OpenAI"]


def test_normalize_trends_rejects_empty_response():
    with pytest.raises(XTrendError, match="without names"):
        _normalize_trends([], 5)


@pytest.mark.asyncio
async def test_missing_cookie_file_is_actionable(tmp_path):
    client = XTrendClient("", "", tmp_path / "missing.json")

    with pytest.raises(XTrendError, match="No X session found"):
        await client.get_trends(5)
