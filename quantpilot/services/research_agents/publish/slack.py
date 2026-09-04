"""One-way Slack delivery: an incoming webhook, or a bot token posting to a channel/DM.

`post()` picks the path from the environment: `QUANTPILOT_SLACK_WEBHOOK_URL`
when set, else `SLACK_BOT_TOKEN` with `QUANTPILOT_SLACK_CHANNEL` (falling back
to `SLACK_ALLOWED_USER_ID`, i.e. a DM to the owner — the same bot the
SecondBrain weekly pipeline uses). There is deliberately no function that
sends unscrubbed text, and no credential ever appears in logs or exceptions.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Callable

from quantpilot.services.research_agents.publish.scrub import ScrubResult, scrub

WEBHOOK_ENV = "QUANTPILOT_SLACK_WEBHOOK_URL"
BOT_TOKEN_ENV = "SLACK_BOT_TOKEN"
CHANNEL_ENV = "QUANTPILOT_SLACK_CHANNEL"
DM_FALLBACK_ENV = "SLACK_ALLOWED_USER_ID"
POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
_MAX_CHARS = 3900  # keep one message comfortably under Slack's limit

Sender = Callable[[str, bytes, dict[str, str], float], tuple[int, str]]


class SlackPostError(RuntimeError):
    pass


def _default_send(url: str, body: bytes, headers: dict[str, str], timeout_s: float) -> tuple[int, str]:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - fixed https hosts  # nosemgrep
        return response.status, response.read().decode("utf-8", errors="replace")


def _prepare(text: str) -> tuple[str, ScrubResult]:
    scrubbed = scrub(text)
    payload = scrubbed.text
    if len(payload) > _MAX_CHARS:
        payload = payload[: _MAX_CHARS - 20].rstrip() + "\n…(truncated)"
    return payload, scrubbed


def _call(url: str, body: bytes, headers: dict[str, str], timeout_s: float, send: Sender) -> tuple[int, str]:
    try:
        return send(url, body, headers, timeout_s)
    except urllib.error.HTTPError as exc:
        raise SlackPostError(f"slack rejected the post: HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise SlackPostError(f"slack unreachable: {type(exc).__name__}") from exc


def post_webhook(text: str, *, url: str | None = None, timeout_s: float = 15.0, send: Sender = _default_send) -> ScrubResult:
    """Scrub, truncate, POST to an incoming webhook. Returns the scrub result for logging."""

    target = url or os.environ.get(WEBHOOK_ENV, "")
    if not target.startswith("https://"):
        raise SlackPostError(f"{WEBHOOK_ENV} is not set to an https webhook")
    payload, scrubbed = _prepare(text)
    body = json.dumps({"text": payload}, ensure_ascii=False).encode("utf-8")
    status, reply = _call(target, body, {"Content-Type": "application/json"}, timeout_s, send)
    if status != 200 or reply.strip() != "ok":
        raise SlackPostError(f"slack webhook returned {status}: {reply[:80]}")
    return scrubbed


def post_bot_message(
    text: str,
    *,
    channel: str | None = None,
    token: str | None = None,
    timeout_s: float = 15.0,
    send: Sender = _default_send,
) -> ScrubResult:
    """Scrub, truncate, chat.postMessage with a bot token. `channel` may be a channel id/name or a user id (DM)."""

    bot_token = token or os.environ.get(BOT_TOKEN_ENV, "")
    target = channel or os.environ.get(CHANNEL_ENV, "") or os.environ.get(DM_FALLBACK_ENV, "")
    if not bot_token:
        raise SlackPostError(f"{BOT_TOKEN_ENV} is not set")
    if not target:
        raise SlackPostError(f"{CHANNEL_ENV} (or {DM_FALLBACK_ENV} for a DM) is not set")
    payload, scrubbed = _prepare(text)
    body = json.dumps({"channel": target, "text": payload}, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8", "Authorization": f"Bearer {bot_token}"}
    status, reply = _call(POST_MESSAGE_URL, body, headers, timeout_s, send)
    try:
        parsed = json.loads(reply) if reply else {}
    except json.JSONDecodeError:
        parsed = {}
    if status != 200 or not parsed.get("ok"):
        # Slack's error field is a short code (e.g. channel_not_found); it never carries the token
        raise SlackPostError(f"slack chat.postMessage failed: {status} {str(parsed.get('error', ''))[:60]}")
    return scrubbed


def delivery_path(environ: dict[str, str] | None = None) -> str:
    """'webhook', 'bot' or '' — which path `post()` will take with this environment."""

    env = os.environ if environ is None else environ
    if env.get(WEBHOOK_ENV, "").startswith("https://"):
        return "webhook"
    if env.get(BOT_TOKEN_ENV, "") and (env.get(CHANNEL_ENV, "") or env.get(DM_FALLBACK_ENV, "")):
        return "bot"
    return ""


def post(text: str, *, send: Sender = _default_send) -> ScrubResult:
    """Deliver through whichever path the environment configures (webhook first, then bot token)."""

    path = delivery_path()
    if path == "webhook":
        return post_webhook(text, send=send)
    if path == "bot":
        return post_bot_message(text, send=send)
    raise SlackPostError(
        f"no Slack delivery configured: set {WEBHOOK_ENV}, or {BOT_TOKEN_ENV} with {CHANNEL_ENV}/{DM_FALLBACK_ENV}"
    )
