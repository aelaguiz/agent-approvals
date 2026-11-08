"""A test hub: what the hub's service does, without the Slack connection and the morning list, so it can run beside
the real hub without taking any of the approver's button presses. Thread replies still reach it, by polling.

Run it with APPROVALS_HOME set to a scratch folder whose config names this machine as the hub, on a port of its
own, with an id_prefix of its own:  APPROVALS_HOME=<folder> APPROVALS_MACHINE=<name> uv run python <this file>
"""
import logging
import threading
import time

from approvals import config, hub, listener


def main():
    config.ensure_dirs()
    logging.basicConfig(level=logging.INFO, filename=str(config.LOGS / "listener.log"),
                        format="%(asctime)s %(levelname)s %(message)s")
    if not config.is_hub():
        raise SystemExit(f"these settings name {config.hub()} as the hub, not {config.machine()}")
    L = listener.Listener()
    hub.serve()
    for every, fn in ((5, L.poll_once), (L.s["sweep_seconds"], L.sweep)):
        threading.Thread(target=L.loop, args=(every, fn), daemon=True).start()
    threading.Thread(target=L.delivery_worker, daemon=True).start()
    logging.getLogger("approvals").info("test hub up on %s, without the Slack connection", config.machine())
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
