"""Run one Qofeno release and receive versioned configuration from Azure Key Vault."""

import json
import logging
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


REPOSITORY = "https://github.com/SohailKhan0525/trend-to-post.git"
ROOT = Path("/var/lib/qofeno/releases")
STOP = threading.Event()
ENV_KEYS = {
    "QOFENO_RELEASE", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN",
    "CLOUDFLARE_D1_API_TOKEN", "D1_DATABASE_ID", "QOFENO_AI_MODEL",
    "DASH_RATTLE_MODEL", "X_AUTH_TOKEN", "X_CT0",
}
REQUIRED_KEYS = {
    "QOFENO_RELEASE", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN",
    "X_AUTH_TOKEN", "X_CT0",
}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# Neither vault auth nor the machine-local metadata request follows redirects.
HTTP = build_opener(ProxyHandler({}), NoRedirect())


def request_json(url, headers):
    with HTTP.open(Request(url, headers=headers), timeout=12) as response:
        return json.load(response)


class Vault:
    def __init__(self, name):
        if not re.fullmatch(r"[a-z0-9-]{3,24}", name):
            raise ValueError("Invalid vault name")
        self.url = f"https://{name}.vault.azure.net/secrets/qofeno-runtime?api-version=7.4"
        self.token = ""
        self.expires = 0

    def read(self):
        if time.time() + 300 >= self.expires:
            query = urlencode({"api-version": "2018-02-01", "resource": "https://vault.azure.net"})
            token = request_json(
                "http://169.254.169.254/metadata/identity/oauth2/token?" + query,
                {"Metadata": "true"},
            )
            self.token = token["access_token"]
            self.expires = int(token["expires_on"])
        try:
            value = request_json(self.url, {"Authorization": "Bearer " + self.token})
        except HTTPError as error:
            if error.code == 401:
                self.expires = 0
            raise
        bundle = json.loads(value["value"])
        revision = bundle["revision"]
        env = bundle["environment"]
        if not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{40}", revision):
            raise ValueError("Invalid release")
        if not isinstance(env, dict) or not REQUIRED_KEYS <= env.keys() <= ENV_KEYS:
            raise ValueError("Invalid runtime environment keys")
        if revision != env["QOFENO_RELEASE"]:
            raise ValueError("Release mismatch")
        if not all(isinstance(v, str) and v and "\x00" not in v for v in env.values()):
            raise ValueError("Invalid runtime environment values")
        return revision, env


def prepare(revision):
    directory = ROOT / revision
    ready = directory / ".qofeno-installed"
    if ready.is_file():
        return directory
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", str(directory)], check=True)
    subprocess.run(
        ["git", "-C", str(directory), "fetch", "--quiet", "--depth=1", "--no-tags",
         REPOSITORY, revision], check=True, timeout=180,
    )
    actual = subprocess.check_output(["git", "-C", str(directory), "rev-parse", "FETCH_HEAD"], text=True).strip()
    if actual != revision:
        raise ValueError("Fetched release differs from the requested commit")
    subprocess.run(["git", "-C", str(directory), "checkout", "--quiet", "--detach", revision], check=True)
    subprocess.run([sys.executable, "-m", "venv", directory / ".venv"], check=True, timeout=180)
    subprocess.run(
        [directory / ".venv/bin/python", "-m", "pip", "install", "--disable-pip-version-check",
         "--no-cache-dir", "--quiet", "-r", "requirements.txt"],
        cwd=directory, check=True, timeout=600,
    )
    ready.touch()
    return directory


def stop_worker(child):
    if child is None or child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=240)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    signal.signal(signal.SIGINT, lambda *_: STOP.set())
    vault = Vault(os.environ["QOFENO_VAULT"])
    child = None
    current = None
    next_read = 0.0
    next_start = 0.0
    try:
        while not STOP.is_set():
            now = time.monotonic()
            if now >= next_read:
                try:
                    desired = vault.read()
                    if desired != current:
                        prepare(desired[0])
                        stop_worker(child)
                        child = None
                        current = desired
                        next_start = 0
                    next_read = time.monotonic() + 300
                except (HTTPError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
                    # Never log request URLs, token payloads, or configuration values.
                    status = getattr(error, "code", None)
                    logging.warning("Runtime configuration unavailable (%s%s).",
                                    type(error).__name__, f" {status}" if status else "")
                    next_read = time.monotonic() + (300 if current else 60)
            if STOP.is_set():
                break
            if child is not None and child.poll() is not None:
                logging.warning("Worker exited with status %s; retrying in one minute.", child.returncode)
                child = None
                next_start = time.monotonic() + 60
            if current and child is None and time.monotonic() >= next_start:
                revision, settings = current
                directory = ROOT / revision
                child = subprocess.Popen(
                    [directory / ".venv/bin/python", "-u", "-m", "app", "serve"],
                    env={**os.environ, **settings, "QOFENO_LEDGER": "d1"}, cwd=directory,
                )
                logging.info("Started Qofeno release %s.", revision)
                # Keep the active and previous checkout; a future rollback can fetch older commits.
                others = sorted(
                    (item for item in ROOT.iterdir() if item != directory and
                     item.is_dir() and re.fullmatch(r"[0-9a-f]{40}", item.name)),
                    key=lambda item: item.stat().st_mtime, reverse=True,
                )
                for old in others[1:]:
                    shutil.rmtree(old, ignore_errors=True)
            STOP.wait(5)
    finally:
        stop_worker(child)


if __name__ == "__main__":
    main()
