import asyncio
import logging
import multiprocessing as mp
from pathlib import Path

from twikit import Client

from .models import Trend

log = logging.getLogger(__name__)

X_REQUEST_TIMEOUT = 30
MAX_ATTEMPTS = 2


class XTrendError(RuntimeError):
    pass


def _fetch_trends_worker(
    auth_token: str,
    ct0: str,
    cookies_file: str,
    limit: int,
    result_queue,
) -> None:
    """Run the X client in a killable child process.

    This is intentional: if the underlying HTTP stack blocks without yielding
    to asyncio, asyncio.wait_for() cannot enforce a timeout in the parent.
    """
    async def run():
        client = Client("en-US", impersonate="chrome124")
        if auth_token and ct0:
            client.set_cookies({
                "auth_token": auth_token,
                "ct0": ct0,
            })
        elif Path(cookies_file).exists():
            client.load_cookies(cookies_file)
        else:
            raise XTrendError(
                "No X session found. Set X_AUTH_TOKEN and X_CT0 in your environment."
            )

        raw = await client.get_trends(
            "trending",
            count=limit,
            retry=False,
        )
        if not raw:
            raise XTrendError("X returned an empty Trends response.")

        result_queue.put(("ok", _normalize_trends(raw, limit)))

    try:
        asyncio.run(run())
    except Exception as exc:
        result_queue.put(("error", f"{type(exc).__name__}: {exc}"))


def _normalize_trends(raw, limit: int) -> list[str]:
    names = []
    seen = set()
    for item in raw:
        name = str(getattr(item, "name", "") or item).strip()
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        names.append(name)
        if len(names) >= limit:
            break
    if not names:
        raise XTrendError("X returned Trends objects without names.")
    return names


class XTrendClient:
    """Read-only X Trends collector. Credentials stay in the environment."""

    def __init__(
        self,
        auth_token: str = "",
        ct0: str = "",
        cookies_file: Path | None = None,
        location: str = "worldwide",
    ):
        self.auth_token = auth_token
        self.ct0 = ct0
        self.cookies_file = cookies_file or Path("data/x_cookies.json")
        self.location = location or "worldwide"

    async def get_trends(self, limit: int) -> list[Trend]:
        if not (self.auth_token and self.ct0) and not self.cookies_file.exists():
            raise XTrendError(
                "No X session found. Set X_AUTH_TOKEN and X_CT0 in your environment."
            )

        ctx = mp.get_context("spawn")
        for attempt in range(1, MAX_ATTEMPTS + 1):
            queue = ctx.Queue()
            process = ctx.Process(
                target=_fetch_trends_worker,
                args=(
                    self.auth_token,
                    self.ct0,
                    str(self.cookies_file),
                    limit,
                    queue,
                ),
            )
            process.start()
            process.join(X_REQUEST_TIMEOUT)

            if process.is_alive():
                process.terminate()
                process.join(5)
                last_error = XTrendError(
                    f"X Trends request timed out after {X_REQUEST_TIMEOUT}s."
                )
            else:
                try:
                    status, payload = queue.get_nowait()
                except Exception:
                    status, payload = "error", (
                        f"X Trends worker exited with code {process.exitcode}."
                    )

                if status == "ok":
                    result = [
                        Trend(name=name, rank=index)
                        for index, name in enumerate(payload, start=1)
                    ]
                    log.info("Collected %d X trends", len(result))
                    queue.close()
                    queue.join_thread()
                    return result

                last_error = XTrendError(payload)

            queue.close()
            queue.join_thread()

            if attempt < MAX_ATTEMPTS:
                delay = 2 ** (attempt - 1)
                log.warning(
                    "X Trends attempt %d/%d failed: %s; retrying in %ss",
                    attempt, MAX_ATTEMPTS, last_error, delay,
                )
                await asyncio.sleep(delay)

        raise XTrendError(
            f"X Trends collection failed after {MAX_ATTEMPTS} attempts: {last_error}"
        )
