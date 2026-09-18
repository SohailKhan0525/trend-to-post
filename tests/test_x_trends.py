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


from app.x_trends import filter_technology_ai_trends
from app.models import Trend


def test_filter_keeps_only_technology_ai_trends():
    trends = [
        Trend("OpenAI", 1),
        Trend("World Cup", 2),
        Trend("NVIDIA", 3),
        Trend("Celebrity News", 4),
    ]
    assert [t.name for t in filter_technology_ai_trends(trends)] == ["OpenAI", "NVIDIA"]


def test_filter_is_case_insensitive():
    assert filter_technology_ai_trends([Trend("Generative AI", 1)])[0].name == "Generative AI"
