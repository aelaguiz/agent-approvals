"""`approvals doctor`: one line per thing the service needs, and a non-zero exit if any fails."""
import json
import shutil
import urllib.request

from . import config


def _check(name, fn):
    try:
        detail = fn()
        print(f"ok    {name}" + (f": {detail}" if detail else ""))
        return True
    except Exception as e:  # report every failure, keep checking
        print(f"FAIL  {name}: {e}")
        return False


def run():
    results = []
    results.append(_check("config", lambda: str(config.CONFIG) if not config.missing() else (_ for _ in ()).throw(
        RuntimeError(f"{config.CONFIG} needs {', '.join(config.missing())} (see config.example.json)"))))
    if not results[-1]:
        return 1

    from . import slackapi
    s = config.settings()

    def bot():
        r = slackapi.client().auth_test()
        return f"{r['user']} in {r['team']}"
    results.append(_check("bot token", bot))

    def channel():
        info = slackapi.client().conversations_info(channel=s["channel"])["channel"]
        if not info.get("is_member"):
            raise RuntimeError(f"the bot isn't in #{info.get('name')}; invite it")
        return "#" + info.get("name", s["channel"])
    results.append(_check("channel", channel))

    def approver():
        u = slackapi.client().users_info(user=s["approver"])["user"]
        return u.get("real_name") or u.get("name")
    results.append(_check("approver", approver))

    def socket_mode():
        req = urllib.request.Request("https://slack.com/api/apps.connections.open", method="POST",
                                     headers={"Authorization": "Bearer " + config.app_token()})
        r = json.load(urllib.request.urlopen(req, timeout=15))
        if not r.get("ok"):
            raise RuntimeError(r.get("error"))
        return "a connection can open"
    results.append(_check("Socket Mode token", socket_mode))

    for tool in ("herdr", "aim"):
        results.append(_check(tool, lambda t=tool: shutil.which(t) or (_ for _ in ()).throw(RuntimeError("not on PATH; closed sessions can't be resumed"))))
    if s.get("video_share_command"):
        results.append(_check("video share command", lambda: " ".join(s["video_share_command"])))
    if s.get("test_approver"):
        print(f"note  test_approver is set ({s['test_approver']}); remove it outside test runs")
    return 0 if all(results) else 1
