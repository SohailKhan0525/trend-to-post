import asyncio
import json
import logging
import multiprocessing as mp
import subprocess
from pathlib import Path

from twikit import Client

from .models import Trend

log = logging.getLogger(__name__)

X_REQUEST_TIMEOUT = 30
MAX_ATTEMPTS = 2
XKIT_TIMEOUT_SECONDS = 25


class XTrendError(RuntimeError):
    pass


def _normalize_trends(raw, limit: int) -> list[str]:
    names = []
    seen = set()
    for item in raw:
        if isinstance(item, dict):
            name = str(
                item.get("headline")
                or item.get("name")
                or item.get("title")
                or ""
            ).strip()
        else:
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


def _xkit_trends(auth_token: str, ct0: str, limit: int) -> list[str]:
    """Primary fallback: X web GraphQL Explore/Trending via xKit.

    xKit uses the same auth_token + ct0 web session and refreshes its
    GraphQL query IDs when X rotates them.
    """
    env = {
        "AUTH_TOKEN": auth_token,
        "CT0": ct0,
        "TWITTER_AUTH_TOKEN": auth_token,
        "TWITTER_CT0": ct0,
    }
    command = [
        "npx",
        "--no-install",
        "@brainwav/xkit",
        "news",
        "--tabs",
        "trending",
        "--no-ai-only",
        "--json",
        "-n",
        str(limit),
    ]
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=XKIT_TIMEOUT_SECONDS,
            env={**__import__("os").environ, **env},
        )
    except FileNotFoundError as exc:
        raise XTrendError("xKit is not installed in the workflow runner.") from exc
    except subprocess.TimeoutExpired as exc:
        raise XTrendError(
            f"xKit X Trends request timed out after {XKIT_TIMEOUT_SECONDS}s."
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()[-800:]
        raise XTrendError(f"xKit X Trends failed: {detail}") from exc

    output = completed.stdout.strip()
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise XTrendError("xKit returned non-JSON output.") from exc

    if isinstance(payload, dict):
        payload = payload.get("news") or payload.get("results") or payload.get("data") or []
    return _normalize_trends(payload, limit)


def _fetch_trends_worker(
    auth_token: str,
    ct0: str,
    cookies_file: str,
    limit: int,
    result_queue,
) -> None:
    """Run X Trends in a killable child process.

    twifork 2.4.x contains the current GenericTimelineById implementation for
    X's Trends surface. The older place-trends endpoint is retained as a
    secondary fallback because X has changed Trends endpoints repeatedly.
    """
    async def run():
        if not auth_token or not ct0:
            raise XTrendError(
                "X_AUTH_TOKEN and X_CT0 are required for the X Trends collector."
            )

        client = Client("en-US", impersonate="chrome124")
        client.set_cookies({
            "auth_token": auth_token,
            "ct0": ct0,
        })

        try:
            raw = await client.get_trends("trending", count=limit, retry=False)
            names = _normalize_trends(raw, limit)
            result_queue.put(("ok", names))
            return
        except Exception as primary_error:
            log.warning(
                "twifork current Trends endpoint failed; trying place Trends backup: %s",
                primary_error,
            )

        try:
            raw = await client.get_place_trends(woeid=1)
            trend_items = (
                raw.get("trends", [])
                if isinstance(raw, dict)
                else getattr(raw, "trends", [])
            )
            if not trend_items:
                raise XTrendError("X returned an empty place Trends response.")
            result_queue.put(("ok", _normalize_trends(trend_items, limit)))
        except Exception as backup_error:
            raise XTrendError(
                f"Both X Trends endpoints failed. primary={primary_error}; backup={backup_error}"
            ) from backup_error

    try:
        asyncio.run(run())
    except Exception as exc:
        result_queue.put(("error", f"{type(exc).__name__}: {exc}"))


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
                    attempt,
                    MAX_ATTEMPTS,
                    last_error,
                    delay,
                )
                await asyncio.sleep(delay)

        raise XTrendError(
            f"X Trends collection failed after {MAX_ATTEMPTS} attempts: {last_error}"
        )
