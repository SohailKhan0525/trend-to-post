import asyncio
import logging
from pathlib import Path

from twikit import Client

from .models import Trend

log = logging.getLogger(__name__)

X_REQUEST_TIMEOUT = 45
MAX_ATTEMPTS = 2


class XTrendError(RuntimeError):
    pass


class XTrendClient:
    """Read-only X Trends collector. Credentials stay in the environment."""

    def __init__(
        self,
        auth_token: str,
        ct0: str,
        cookies_file: Path,
        location: str = "worldwide",
    ):
        self.auth_token = auth_token
        self.ct0 = ct0
        self.cookies_file = cookies_file
        self.location = location or "worldwide"
        self.client = Client("en-US", impersonate="chrome124")

    async def _load_session(self) -> None:
        try:
            if self.auth_token and self.ct0:
                self.client.set_cookies({
                    "auth_token": self.auth_token,
                    "ct0": self.ct0,
                })
            elif self.cookies_file.exists():
                self.client.load_cookies(str(self.cookies_file))
            else:
                raise XTrendError(
                    "No X session found. Set X_AUTH_TOKEN and X_CT0 in your environment."
                )
        except XTrendError:
            raise
        except Exception as exc:
            raise XTrendError(f"Unable to initialize X session: {exc}") from exc

    async def get_trends(self, limit: int) -> list[Trend]:
        await self._load_session()

        last_error = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                # twifork's Trends API supports retry=False. This is important here:
                # its default internal retry loop can otherwise keep a failed Trends
                # request alive indefinitely.
                raw = await asyncio.wait_for(
                    self.client.get_trends(
                        "trending",
                        count=limit,
                        retry=False,
                    ),
                    timeout=X_REQUEST_TIMEOUT,
                )
                if not raw:
                    raise XTrendError("X returned an empty Trends response.")

                result = []
                seen = set()

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

                log.info("Collected %d X trends", len(result))
                return result

            except asyncio.TimeoutError as exc:
                last_error = XTrendError(
                    f"X Trends request timed out after {X_REQUEST_TIMEOUT}s."
                )
            except Exception as exc:
                last_error = exc

            if attempt < MAX_ATTEMPTS:
                delay = 2 ** (attempt - 1)
                log.warning(
                    "X Trends attempt %d/%d failed: %s; retrying in %ss",
                    attempt, MAX_ATTEMPTS, last_error, delay
                )
                await asyncio.sleep(delay)

        raise XTrendError(
            f"X Trends collection failed after {MAX_ATTEMPTS} attempts: {last_error}"
        )
