import asyncio
import logging
from pathlib import Path

from twikit import Client

from .models import Trend

log = logging.getLogger(__name__)


class XTrendError(RuntimeError):
    pass


class XTrendClient:
    """Read-only X Trends collector.

    Authentication is cookie-based because current X web login flows are not
    reliable for non-browser clients. This class never calls any write method.
    """

    def __init__(self, cookies_file: Path, location: str = "worldwide"):
        self.cookies_file = cookies_file
        self.location = location or "worldwide"
        self.client = Client("en-US", impersonate="chrome124")

    async def _load_session(self) -> None:
        if not self.cookies_file.exists():
            raise XTrendError(
                f"X cookies not found at {self.cookies_file}. "
                "Export a fresh X session cookie file locally and retry."
            )

        try:
            self.client.load_cookies(str(self.cookies_file))
            logged_in = await self.client.is_logged_in()
        except Exception as exc:
            raise XTrendError(f"Unable to load/validate X cookies: {exc}") from exc

        if not logged_in:
            raise XTrendError(
                "The saved X cookies are expired or invalid. Export a fresh session."
            )

    async def get_trends(self, limit: int) -> list[Trend]:
        await self._load_session()

        last_error = None
        for attempt in range(1, 4):
            try:
                raw = await self.client.get_trends("trending")
                if not raw:
                    raise XTrendError("X returned an empty Trends response.")

                result: list[Trend] = []
                seen: set[str] = set()

                for item in raw:
                    name = str(getattr(item, "name", "") or item).strip()
                    key = name.casefold()
                    if not name or key in seen:
                        continue
                    seen.add(key)
                    result.append(Trend(name=name, rank=len(result) + 1))
                    if len(result) >= limit:
                        break

                if not result:
                    raise XTrendError("X returned Trends objects without names.")

                log.info(
                    "Collected %d X trends (location setting: %s)",
                    len(result),
                    self.location,
                )
                return result

            except Exception as exc:
                last_error = exc
                if attempt < 3:
                    delay = 2 ** (attempt - 1)
                    log.warning(
                        "X Trends attempt %d/3 failed: %s; retrying in %ss",
                        attempt,
                        exc,
                        delay,
                    )
                    await asyncio.sleep(delay)

        raise XTrendError(f"X Trends collection failed after 3 attempts: {last_error}")
