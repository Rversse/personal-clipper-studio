from __future__ import annotations
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])

def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/smoke_test_packaged_backend.py <pcs-api.exe>")
    executable = Path(sys.argv[1]).resolve()
    if not executable.is_file():
        raise SystemExit(f"Packaged backend executable not found: {executable}")

    port = _free_loopback_port()
    with tempfile.TemporaryDirectory(prefix="pcs-backend-smoke-") as temporary:
        work_dir = Path(temporary)
        env = os.environ.copy()
        env["PCS_DATA_DIR"] = str(work_dir / "data")
        env["PYTHONUTF8"] = "1"
        stdout_path = work_dir / "backend.stdout.log"
        stderr_path = work_dir / "backend.stderr.log"
        with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open(
            "w", encoding="utf-8"
        ) as stderr:
            process = subprocess.Popen(
                [str(executable), "--port", str(port)],
                cwd=work_dir,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
            )
            try:
                deadline = time.monotonic() + 60
                health_url = f"http://127.0.0.1:{port}/api/health"
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError(
                            "Packaged backend exited before becoming healthy. "
                            f"stdout: {stdout_path.read_text(encoding='utf-8', errors='replace')} "
                            f"stderr: {stderr_path.read_text(encoding='utf-8', errors='replace')}"
                        )
                    try:
                        with urlopen(health_url, timeout=2) as response:
                            payload = json.loads(response.read().decode("utf-8"))
                        if payload.get("status") == "ok" and payload.get("service") == "personal-clipper-local-api":
                            print("Packaged backend health smoke test passed.")
                            return 0
                    except (URLError, TimeoutError, ConnectionError, json.JSONDecodeError):
                        time.sleep(0.25)
                raise TimeoutError(
                    "Packaged backend did not become healthy within 60 seconds. "
                    f"stdout: {stdout_path.read_text(encoding='utf-8', errors='replace')} "
                    f"stderr: {stderr_path.read_text(encoding='utf-8', errors='replace')}"
                )
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
