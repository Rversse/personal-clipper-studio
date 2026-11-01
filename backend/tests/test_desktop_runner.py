from __future__ import annotations
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND_ROOT = str(Path(__file__).resolve().parents[1])
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from desktop_runner import main

def test_runner_binds_only_to_loopback(monkeypatch):
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=fake_run))
    main(["--port", "18765"])
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == ("app.main:app",)
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 18765
    assert kwargs["access_log"] is False

@pytest.mark.parametrize("value", ["0", "80", "65536", "not-a-port"])
def test_runner_rejects_invalid_ports(value):
    with pytest.raises(SystemExit):
        main(["--port", value])
