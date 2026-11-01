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
from urllib.request import Request, urlopen


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _read_json(url: str) -> dict:
    with urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _stop_process_gracefully(process: subprocess.Popen[str], timeout: float = 15) -> None:
    """Ask the packaged server to stop cleanly, then enforce a bounded fallback."""
    if process.poll() is not None:
        if process.returncode != 0:
            raise RuntimeError(f"Packaged backend exited with non-zero status: {process.returncode}")
        return

    if process.stdin is not None:
        try:
            process.stdin.write("shutdown\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        finally:
            process.stdin.close()

    try:
        exit_code = process.wait(timeout=timeout)
        if exit_code != 0:
            raise RuntimeError(f"Packaged backend exited with non-zero status: {exit_code}")
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


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
                stdin=subprocess.PIPE,
                stdout=stdout,
                stderr=stderr,
                text=True,
                encoding="utf-8",
            )
            base_url = f"http://127.0.0.1:{port}"
            try:
                deadline = time.monotonic() + 60
                healthy = False
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError(
                            "Packaged backend exited before becoming healthy. "
                            f"stdout: {stdout_path.read_text(encoding='utf-8', errors='replace')} "
                            f"stderr: {stderr_path.read_text(encoding='utf-8', errors='replace')}"
                        )
                    try:
                        payload = _read_json(f"{base_url}/api/health")
                        healthy = (
                            payload.get("status") == "ok"
                            and payload.get("service") == "personal-clipper-local-api"
                        )
                        if healthy:
                            break
                    except (URLError, TimeoutError, ConnectionError, json.JSONDecodeError):
                        time.sleep(0.25)

                if not healthy:
                    raise TimeoutError(
                        "Packaged backend did not become healthy within 60 seconds. "
                        f"stdout: {stdout_path.read_text(encoding='utf-8', errors='replace')} "
                        f"stderr: {stderr_path.read_text(encoding='utf-8', errors='replace')}"
                    )

                create_request = Request(
                    f"{base_url}/api/projects",
                    data=json.dumps({
                        "name": "Packaged backend smoke test",
                        "description": "SQLite API integration check",
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(create_request, timeout=10) as response:
                    created = json.loads(response.read().decode("utf-8"))
                if not created.get("id") or created.get("name") != "Packaged backend smoke test":
                    raise AssertionError(f"Unexpected project creation response: {created}")

                projects = _read_json(f"{base_url}/api/projects")
                if not any(project.get("id") == created["id"] for project in projects):
                    raise AssertionError("Created project was not returned by the list endpoint.")

                _stop_process_gracefully(process)
                if process.poll() is None:
                    raise RuntimeError("Packaged backend remained alive after graceful shutdown.")
                print("Packaged backend health, SQLite create/list, and graceful shutdown tests passed.")
            finally:
                if process.poll() is None:
                    _stop_process_gracefully(process, timeout=5)
                if process.stdin is not None and not process.stdin.closed:
                    process.stdin.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
