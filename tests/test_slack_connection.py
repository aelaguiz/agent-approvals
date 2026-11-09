"""The hub's Slack connection keeps working after a TLS write fails on the thread that reads it.

localhost.pem is a self-signed certificate and key for 127.0.0.1, made for these tests only.
"""
import queue
import socket
import ssl
import threading
import time
from pathlib import Path

from slack_sdk import WebClient

from approvals import listener

PEM = str(Path(__file__).with_name("localhost.pem"))


def tls_server(plan):
    """A loopback TLS server. Each connection takes the next word from `plan`: "close" ends it right after the
    handshake; "hello" sends b"hello" a moment later, so the client's read has to wait for it."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(PEM)
    srv = socket.create_server(("127.0.0.1", 0))

    def later(conn):
        time.sleep(0.1)
        conn.sendall(b"hello")
        time.sleep(1)
        conn.close()

    def serve():
        while True:
            conn = ctx.wrap_socket(srv.accept()[0], server_side=True)
            if plan.get() == "close":
                conn.close()
            else:
                threading.Thread(target=later, args=(conn,), daemon=True).start()
    threading.Thread(target=serve, daemon=True).start()
    return srv.getsockname()[1]


def test_a_read_after_a_failed_write_on_the_same_thread_gets_its_data():
    plan = queue.Queue()
    port = tls_server(plan)
    ctx = ssl.create_default_context(cafile=PEM)

    def connect():
        return ctx.wrap_socket(socket.create_connection(("127.0.0.1", port), 3), server_hostname="localhost")

    # Like Slack's client: connections are opened on one thread and read and written on another.
    jobs, results = queue.Queue(), queue.Queue()

    def reader():
        while True:
            job = jobs.get()
            try:
                results.put(job())
            except Exception as e:
                results.put(e)
    threading.Thread(target=reader, daemon=True).start()

    plan.put("close")
    dead = connect()

    def write_until_it_fails():
        for _ in range(100):
            dead.send(b"x")
            time.sleep(0.01)
    jobs.put(write_until_it_fails)
    assert isinstance(results.get(timeout=5), OSError)

    plan.put("hello")
    fresh = connect()

    def read():
        listener.clear_tls_errors()
        return fresh.recv(16)
    jobs.put(read)
    assert results.get(timeout=5) == b"hello"


def test_slacks_client_reads_each_connection_through_the_clearing_step(monkeypatch):
    cleared = threading.Event()
    monkeypatch.setattr(listener, "clear_tls_errors", cleared.set)
    sm = listener.SlackConnection(app_token="xapp-made-up", web_client=WebClient())
    try:
        assert cleared.wait(2)
    finally:
        sm.message_processor.event.set()
        sm.message_queue.put(None)      # wakes the message thread so close() doesn't wait out its 1 s poll
        sm.close()
