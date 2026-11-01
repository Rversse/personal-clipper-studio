from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND_ROOT = str(Path(__file__).resolve().parents[1])
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from desktop_runner import _watch_shutdown, main


class FakeServer:
    def __init__(self, config):
        self.config = config
        self.should_exit = False
        self.ran = False

    def run(self):
        self.ran = True


def test_runner_binds_only_to_loopback(monkeypatch):
    config_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    servers: list[FakeServer] = []

    def fake_config(*args, **kwargs):
        config_calls.append((args, kwargs))
        return SimpleNamespace(args=args, kwargs=kwargs)

    def fake_server(config):
        server = FakeServer(config)
        servers.append(server)
        return server

    monkeypatch.setitem(
        sys.modules,
        "uvicorn",
        SimpleNamespace(Config=fake_config, Server=fake_server),
    )
    monkeypatch.setattr("sys.stdin", StringIO(""))

    main(["--port", "18765"])

    assert len(config_calls) == 1
    args, kwargs = config_calls[0]
    assert args == ("app.main:app",)
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 18765
    assert kwargs["access_log"] is False
    assert kwargs["timeout_graceful_shutdown"] == 8
    assert len(servers) == 1
    assert servers[0].ran is True


def test_shutdown_watcher_requires_explicit_command():
    server = FakeServer(config=None)

    _watch_shutdown(server, StringIO("status\nnot-shutdown\nshutdown\nignored\n"))

    assert server.should_exit is True


def test_shutdown_watcher_does_not_shutdown_on_eof_or_unrelated_input():
    server = FakeServer(config=None)

    _watch_shutdown(server, StringIO("status\nclose\n"))

    assert server.should_exit is False


@pytest.mark.parametrize("value", ["0", "80", "65536", "not-a-port"])
def test_runner_rejects_invalid_ports(value):
    with pytest.raises(SystemExit):
        main(["--port", value])
