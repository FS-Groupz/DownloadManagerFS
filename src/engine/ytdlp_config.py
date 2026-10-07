"""
Shared yt-dlp configuration for Download Manager FS.

YouTube blocks most datacenter/cloud IPs with "Sign in to confirm you're not a bot".
This module centralises the options that help (cookies, JS runtime, proxy) and turns
raw yt-dlp errors into actionable messages.

Configuration (all optional, via environment variables):
    FSDM_COOKIES_FILE      Path to a Netscape-format cookies.txt exported from a logged-in browser.
                           Fallbacks: ~/.config/download-manager-fs/cookies.txt, ./cookies.txt
    FSDM_COOKIES_BROWSER   e.g. "firefox" or "chrome" (only works where a browser profile exists, e.g. a PC).
    FSDM_PROXY             e.g. "http://user:pass@host:port" (a residential proxy avoids datacenter-IP blocks).
    FSDM_JS_RUNTIME        "deno" (default) or "node" / "bun" / "quickjs" -- required by modern YouTube extraction.
"""

import os
import shutil
from typing import Any, Dict, Optional

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def find_cookies_file() -> Optional[str]:
    candidates = [
        os.environ.get("FSDM_COOKIES_FILE", ""),
        os.path.expanduser("~/.config/download-manager-fs/cookies.txt"),
        os.path.join(_PROJECT_ROOT, "cookies.txt"),
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def _detect_js_runtime() -> Optional[str]:
    preferred = os.environ.get("FSDM_JS_RUNTIME", "").strip().lower()
    order = [preferred] if preferred else []
    order += ["deno", "node", "bun"]
    for name in order:
        if name and shutil.which(name):
            return name
    return None


def apply_ytdlp_options(opts: Dict[str, Any]) -> Dict[str, Any]:
    """Mutates and returns `opts` with cookies / JS runtime / proxy settings applied."""
    cookies = find_cookies_file()
    if cookies:
        opts["cookiefile"] = cookies
    else:
        browser = os.environ.get("FSDM_COOKIES_BROWSER", "").strip()
        if browser:
            opts["cookiesfrombrowser"] = (browser,)

    proxy = os.environ.get("FSDM_PROXY", "").strip()
    if proxy:
        opts["proxy"] = proxy

    runtime = _detect_js_runtime()
    if runtime:
        opts["js_runtimes"] = {runtime: {}}
    # Lets yt-dlp fetch the YouTube JS challenge solver script (needs a JS runtime above).
    opts["remote_components"] = ["ejs:github"]

    opts.setdefault("retries", 3)
    opts.setdefault("socket_timeout", 30)
    return opts


def friendly_error(message: str) -> str:
    """Convert raw yt-dlp errors into short, actionable text (keeps a stable [CODE] prefix for the UI)."""
    m = (message or "").lower()
    if "confirm you" in m and "bot" in m:
        return ("[BOT_CHECK] YouTube is blocking this server's IP. Add a cookies.txt "
                "(FSDM_COOKIES_FILE) or use a residential proxy (FSDM_PROXY). " + (message or "")[:160])
    if "failed to extract any player response" in m or "unable to extract" in m:
        return ("[YTDLP_OUTDATED] YouTube changed something. Run: pip install -U \"yt-dlp[default]\" "
                "and make sure deno (or node) is installed. " + (message or "")[:160])
    if "private video" in m or "members-only" in m or "sign in" in m and "age" in m:
        return "[LOGIN_REQUIRED] This video needs a logged-in account (cookies.txt). " + (message or "")[:160]
    return message
