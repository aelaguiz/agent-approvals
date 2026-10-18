"""Thin wrapper over Slack's Web API, as ops_bot."""
import functools
import re
import subprocess
import time
from pathlib import Path

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from slack_sdk.http_retry.builtin_handlers import RateLimitErrorRetryHandler

from . import config



@functools.lru_cache(maxsize=1)
def client():
    c = WebClient(token=config.bot_token())
    c.retry_handlers.append(RateLimitErrorRetryHandler(max_retry_count=5))
    return c


def channel():
    return config.require()["channel"]


def _media_retry(call):
    """An image uploaded a moment ago can be refused until Slack finishes processing it."""
    for attempt in range(8):
        try:
            return call()
        except SlackApiError as e:
            if "slack_file" not in str(e) or attempt == 7:
                raise
            time.sleep(2)


def post(blocks, text, thread_ts=None, broadcast=False):
    kw = dict(channel=channel(), blocks=blocks, text=text, unfurl_links=False, unfurl_media=False)
    if thread_ts:
        kw["thread_ts"] = thread_ts
        if broadcast:
            kw["reply_broadcast"] = True
    return _media_retry(lambda: client().chat_postMessage(**kw)["ts"])


def update(ts, blocks, text):
    _media_retry(lambda: client().chat_update(channel=channel(), ts=ts, blocks=blocks, text=text))


def note(thread_ts, text):
    """A plain one-line reply in a thread."""
    return client().chat_postMessage(channel=channel(), thread_ts=thread_ts, text=text, unfurl_links=False)["ts"]


def react(ts, name):
    try:
        client().reactions_add(channel=channel(), timestamp=ts, name=name)
    except Exception as e:  # already_reacted is fine
        if "already_reacted" not in str(e):
            raise


def ephemeral(user, text, thread_ts=None):
    kw = dict(channel=channel(), user=user, text=text)
    if thread_ts:
        kw["thread_ts"] = thread_ts
    client().chat_postEphemeral(**kw)


def history(oldest):
    return client().conversations_history(channel=channel(), oldest=oldest, limit=200)["messages"]


def replies(ts, oldest=None):
    kw = dict(channel=channel(), ts=ts, limit=200)
    if oldest:
        kw["oldest"] = oldest
    return client().conversations_replies(**kw)["messages"]


def permalink(ts):
    return client().chat_getPermalink(channel=channel(), message_ts=ts)["permalink"]


def delete(ts):
    client().chat_delete(channel=channel(), ts=ts)


def upload_private_image(path, title):
    """Upload without sharing anywhere; the card shows it through an image block."""
    r = client().files_upload_v2(file=str(path), title=title[:200])
    return r["file"]["id"]


def upload_to_thread(path, thread_ts, title, comment=None):
    """Returns the uploaded file's permalink."""
    r = client().files_upload_v2(file=str(path), title=title[:200], channel=channel(), thread_ts=thread_ts, initial_comment=comment)
    f = r.get("file") or (r.get("files") or [{}])[0]
    return f.get("permalink")


@functools.lru_cache(maxsize=1)
def bot_user():
    return client().auth_test()["user_id"]


def share_video(path, title, description):
    """Publish a video with the configured share command and return the page that plays it, or None if none is set."""
    cmd = config.settings().get("video_share_command")
    if not cmd:
        return None
    out = subprocess.run([*cmd, "--title", title[:90], "--description", description[:200], str(path)],
                         capture_output=True, text=True, timeout=600)
    m = re.search(r"^URL: (\S+)", out.stdout, re.M)
    if out.returncode != 0 or not m:
        raise RuntimeError("cf-share failed: " + (out.stderr or out.stdout)[-500:])
    return m.group(1)
