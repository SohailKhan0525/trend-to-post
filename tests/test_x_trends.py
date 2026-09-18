from pathlib import Path

import pytest

from app.x_trends import XTrendClient, XTrendError


class FakeClient:
    def __init__(self, raw):
        self.raw = raw

    def load_cookies(self, path):
        assert path.endswith("cookies.json")

    async def is_logged_in(self):
        return True

    async def get_trends(self, kind):
        assert kind == "trending"
        return self.raw


@pytest.mark.asyncio
async def test_get_trends_deduplicates_and_limits(tmp_path):
    cookie_file = tmp_path / "cookies.json"
    cookie_file.write_text("{}")

    client = XTrendClient(cookie_file)
    client.client = FakeClient([
        type("Trend", (), {"name": "AI"})(),
        type("Trend", (), {"name": "ai"})(),
        type("Trend", (), {"name": "OpenAI"})(),
        type("Trend", (), {"name": ""})(),
        type("Trend", (), {"name": "Robotics"})(),
    ])

    result = await client.get_trends(2)

    assert [item.name for item in result] == ["AI", "OpenAI"]
    assert [item.rank for item in result] == [1, 2]


@pytest.mark.asyncio
async def test_missing_cookie_file_is_actionable(tmp_path):
    client = XTrendClient(tmp_path / "missing.json")

    with pytest.raises(XTrendError, match="X cookies not found"):
        await client.get_trends(5)
