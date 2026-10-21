"""
Slack side of the server: verify Events API requests, turn a request message
into a job, and reply in its thread when the job starts and finishes.

Setup (Slack app, see README in .env.example):
  - Event Subscriptions -> Request URL: <PUBLIC_BASE_URL>/slack/events,
    bot events: message.channels (and message.groups for a private channel)
  - Bot token scopes: chat:write, channels:history (groups:history)
  - Invite the bot to the requests channel; set SLACK_CHANNEL_ID to it.
Only top-level messages in SLACK_CHANNEL_ID that look like a request form
(contain a "Product" label) are picked up; thread replies are ignored.
"""

import hashlib
import hmac
import json
import os
import time
import urllib.request

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET", "")
SLACK_CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID", "")


def enabled() -> bool:
    return bool(SLACK_BOT_TOKEN and SLACK_SIGNING_SECRET)


def verify(headers, body: bytes) -> bool:
    """Slack request signing (v0)."""
    ts = headers.get("x-slack-request-timestamp", "")
    sig = headers.get("x-slack-signature", "")
    if not ts.isdigit() or abs(time.time() - int(ts)) > 300:
        return False
    base = f"v0:{ts}:".encode() + body
    expected = "v0=" + hmac.new(SLACK_SIGNING_SECRET.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def is_request_message(event: dict) -> bool:
    if event.get("type") != "message" or event.get("subtype") not in (None, "bot_message"):
        return False
    if event.get("thread_ts") and event.get("thread_ts") != event.get("ts"):
        return False  # a reply in a thread, not a new request
    if SLACK_CHANNEL_ID and event.get("channel") != SLACK_CHANNEL_ID:
        return False
    text = event.get("text") or ""
    return "product" in text.lower() and "@" in text


def message_text(event: dict) -> str:
    """Workflow Builder posts can carry the form in blocks rather than `text`."""
    text = event.get("text") or ""
    if "\n" in text:
        return text
    parts = []
    for block in event.get("blocks") or []:
        for el in block.get("elements") or []:
            for sub in el.get("elements") or []:
                if sub.get("type") == "text":
                    parts.append(sub.get("text", ""))
                elif sub.get("type") == "link":
                    parts.append(sub.get("text") or sub.get("url", ""))
    return "".join(parts) or text


def post(channel: str, thread_ts: str, text: str) -> None:
    if not SLACK_BOT_TOKEN:
        return
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=json.dumps({"channel": channel, "thread_ts": thread_ts, "text": text,
                         "unfurl_links": False}).encode(),
        headers={"Content-Type": "application/json; charset=utf-8",
                 "Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = json.loads(resp.read() or b"{}")
    if not body.get("ok"):
        print(f"[SLACK] chat.postMessage failed: {body.get('error')}")


def finished_text(job_id: int, data: dict, status: str, job_url: str) -> str:
    users = data["users"]
    if status == "failed":
        head = f":x: Request #{job_id} failed: {data.get('error') or 'see the log'}"
    elif status == "done_with_errors":
        bad = [u for u in users if u["errors"]]
        head = f":warning: Request #{job_id} finished with errors for {len(bad)} of {len(users)} user(s)"
    else:
        head = f":white_check_mark: Request #{job_id} done — {len(users)} user(s) set up"
    lines = [head, f"Emails and keys: {job_url}"]
    for u in users:
        if u["errors"]:
            lines.append(f"• {u['name']} ({u['email']}): {'; '.join(u['errors'])}")
    return "\n".join(lines)
