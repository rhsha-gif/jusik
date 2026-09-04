"""One-way Slack delivery through an incoming webhook.

There is deliberately no function that posts unscrubbed text. The webhook URL
is itself a credential and never appears in logs or exception messages.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Callable

from quantpilot.services.research_agents.publish.scrub import ScrubResult, scrub

WEBHOOK_ENV = "QUANTPILOT_SLACK_WEBHOOK_URL"
_MAX_CHARS = 3900  # keep one webhook message comfortably under Slack's limit


class SlackPostError(RuntimeError):
    pass


def _default_send(url: str, body: bytes, timeout_s: float) -> tuple[int, str]:
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - fixed https host  # nosemgrep
        return response.status, response.read().decode("utf-8", errors="replace")


def post_webhook(
    text: str,
    *,
    url: str | None = None,
    timeout_s: float = 15.0,
    send: Callable[[str, bytes, float], tuple[int, str]] = _default_send,
) -> ScrubResult:
    """Scrub, truncate, POST. Returns the scrub result so the caller can log replacements."""

    target = url or os.environ.get(WEBHOOK_ENV, "")
    if not target.startswith("https://"):
        raise SlackPostError(f"{WEBHOOK_ENV} is not set to an https webhook")
    scrubbed = scrub(text)
    payload = scrubbed.text
    if len(payload) > _MAX_CHARS:
        payload = payload[: _MAX_CHARS - 20].rstrip() + "\n…(truncated)"
    body = json.dumps({"text": payload}, ensure_ascii=False).encode("utf-8")
    try:
        status, reply = send(target, body, timeout_s)
    except urllib.error.HTTPError as exc:
        raise SlackPostError(f"slack webhook rejected the post: HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise SlackPostError(f"slack webhook unreachable: {type(exc).__name__}") from exc
    if status != 200 or reply.strip() != "ok":
        raise SlackPostError(f"slack webhook returned {status}: {reply[:80]}")
    return scrubbed
