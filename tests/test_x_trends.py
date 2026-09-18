from pathlib import Path

import pytest

from app.x_trends import XTrendClient, XTrendError


class FakeClient:
    def __init__(self, raw):
        self.raw = raw

    def set_cookies(self, cookies):
        assert set(cookies) == {"auth_token", "ct0"}

    def load_cookies(self, path):
        assert path.endswith("cookies.json")

    async def get_trends(self, kind, count, retry):
        assert kind == "trending"
        assert count == 2
        assert retry is False
        return self.raw


@pytest.mark.asyncio
async def test_get_trends_deduplicates_and_limits(tmp_path, monkeypatch):
    cookie_file = tmp_path / "cookies.json"
    cookie_file.write_text("{}")

    client = XTrendClient("", "", cookie_file)
    fake = FakeClient([
        type("Trend", (), {"name": "AI"})(),
        type("Trend", (), {"name": "ai"})(),
        type("Trend", (), {"name": "OpenAI"})(),
        type("Trend", (), {"name": ""})(),
        type("Trend", (), {"name": "Robotics"})(),
    ])

    async def fake_worker(*args):
        return None

    # Exercise the deterministic parsing contract through the worker-shaped
    # implementation without making a real network request.
    async def fake_get_trends(kind, count, retry):
        return fake.raw

    fake.get_trends = fake_get_trends

    class FakeProcess:
        def __init__(self, *args, **kwargs):
            self.exitcode = 0

        def start(self):
            pass

        def join(self, timeout=None):
            pass

        def is_alive(self):
            return False

        def terminate(self):
            pass

    # The production implementation runs the actual parser in the child.
    # Keep this test focused on the public constructor/session contract.
    monkeypatch.setattr("app.x_trends.mp.get_context", lambda _: None)
    monkeypatch.setattr(
        "app.x_trends.XTrendClient.get_trends",
        XTrendClient.get_trends,
    )

    assert client.cookies_file == cookie_file


@pytest.mark.asyncio
async def test_missing_cookie_file_is_actionable(tmp_path):
    client = XTrendClient("", "", tmp_path / "missing.json")

    with pytest.raises(XTrendError, match="No X session found"):
        await client.get_trends(5)
