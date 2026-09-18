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


NICHE_KEYWORDS = (
    "artificial intelligence", "machine learning", "deep learning", "generative ai",
    "large language model", "llm", "chatgpt", "openai", "gemini", "claude",
    "anthropic", "copilot", "ai agent", "agentic", "neural network", "robotics",
    "robot", "autonomous", "gpu", "nvidia", "amd", "intel", "semiconductor", "chip",
    "processor", "cpu", "data center", "cloud", "cybersecurity", "software",
    "programming", "coding", "developer", "github", "microsoft", "google", "apple",
    "iphone", "android", "meta", "amazon web services", "aws", "technology", "tech",
)


def is_technology_ai_trend(name: str) -> bool:
    normalized = name.casefold().strip()
    return any(keyword in normalized for keyword in NICHE_KEYWORDS)


def filter_technology_ai_trends(trends: list[Trend]) -> list[Trend]:
    return [trend for trend in trends if is_technology_ai_trend(trend.name)]


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

        # X exposes multiple live Trends surfaces. A single "trending" board
        # can legitimately contain no Technology/AI topics, so collect the
        # non-sports/non-entertainment trend surfaces and let the niche filter
        # decide what is usable. This remains X Trends-only.
        collected = []
        errors = []
        for category in ("trending", "news", "for-you"):
            try:
                raw = await client.get_trends(category, count=limit, retry=False)
                names = _normalize_trends(raw, limit)
                collected.extend(names)
                log.info("Collected %d X %s trends", len(names), category)
            except Exception as exc:
                errors.append(f"{category}={exc}")
                log.warning("X %s Trends endpoint failed: %s", category, exc)

        if collected:
            result_queue.put(("ok", _normalize_trends(collected, limit * 3)))
            return

        # Keep the legacy place endpoint as a final compatibility fallback.
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
            detail = "; ".join(errors) or "no category response"
            raise XTrendError(
                f"X Trends category endpoints failed. categories={detail}; "
                f"place_backup={backup_error}"
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
