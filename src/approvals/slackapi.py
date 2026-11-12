"""Thin wrapper over Slack's Web API, as ops_bot."""
import functools
import os
import re
import subprocess
import time
import urllib.request
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


def unreact(ts, name):
    try:
        client().reactions_remove(channel=channel(), timestamp=ts, name=name)
    except Exception as e:  # no_reaction is fine
        if "no_reaction" not in str(e):
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


@functools.lru_cache(maxsize=1)
def _team_url():
    return client().auth_test()["url"]


def message_url(ts):
    """A link to a message in the approvals channel, without an API call per message."""
    return f"{_team_url()}archives/{channel()}/p{ts.replace('.', '')}"


def views_open(trigger_id, view):
    client().views_open(trigger_id=trigger_id, view=view)


def views_update(view_id, view, hash_=None):
    client().views_update(view_id=view_id, view=view, **({"hash": hash_} if hash_ else {}))


def delete(ts):
    client().chat_delete(channel=channel(), ts=ts)


def upload_private_image(path, title):
    """Upload without sharing anywhere; the card shows it through an image block."""
    r = client().files_upload_v2(file=str(path), title=title[:200])
    return r["file"]["id"]


def upload_pictures_to_thread(items, thread_ts, comment):
    """Post pictures in a thread, in one message: items are (path, title). Slack shows a picture inside a carousel
    card only once it's shared where the card is, so a card's pictures go in its thread, full size, which is also
    where the approver can open them. Returns [{"id", "url"}] in order, once Slack has processed them."""
    r = client().files_upload_v2(file_uploads=[{"file": str(p), "title": t[:200]} for p, t in items],
                                 channel=channel(), thread_ts=thread_ts, initial_comment=comment)
    files = r.get("files") or [r["file"]]
    out = [{"id": f["id"], "url": f.get("permalink")} for f in files]
    deadline = time.monotonic() + 30
    for f in out:
        while time.monotonic() < deadline:
            info = client().files_info(file=f["id"])["file"]
            f["url"] = info.get("permalink") or f["url"]
            if info.get("original_w") or info.get("thumb_360"):
                break
            time.sleep(1)
    return out


def upload_to_thread(path, thread_ts, title, comment=None):
    """Returns the uploaded file's permalink."""
    r = client().files_upload_v2(file=str(path), title=title[:200], channel=channel(), thread_ts=thread_ts, initial_comment=comment)
    f = r.get("file") or (r.get("files") or [{}])[0]
    return f.get("permalink")


def download(file_id, folder, name):
    """Save an uploaded file into folder as name plus its own extension; returns the path."""
    f = client().files_info(file=file_id)["file"]
    path = Path(folder) / (name + (Path(f.get("name") or "").suffix or f".{f.get('filetype') or 'bin'}"))
    req = urllib.request.Request(f["url_private_download"], headers={"Authorization": f"Bearer {config.bot_token()}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        path.write_bytes(r.read())
    return path


@functools.lru_cache(maxsize=1)
def bot_user():
    return client().auth_test()["user_id"]


def share_video(path, title, description):
    """Publish a video with the configured share command and return the page that plays it, or None if none is set."""
    cmd = config.settings().get("video_share_command")
    if not cmd:
        return None
    out = subprocess.run([*(os.path.expanduser(c) for c in cmd), "--title", title[:90], "--description", description[:200], str(path)],
                         capture_output=True, text=True, timeout=600)
    m = re.search(r"^URL: (\S+)", out.stdout, re.M)
    if out.returncode != 0 or not m:
        raise RuntimeError("cf-share failed: " + (out.stderr or out.stdout)[-500:])
    return m.group(1)
