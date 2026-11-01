from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _read_json(url: str, headers: dict[str, str] | None = None) -> dict:
    request = Request(url, headers=headers or {})
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _request_json(
    url: str,
    method: str,
    payload: dict | None = None,
    headers: dict[str, str] | None = None,
) -> dict:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request_headers = dict(headers or {})
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=request_headers, method=method)
    with urlopen(request, timeout=10) as response:
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

    try:
        runtime_check = subprocess.run(
            [str(executable), "--self-check-runtime"],
            cwd=executable.parent,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise TimeoutError(
            "Packaged transcription runtime imports did not finish within 60 seconds."
        ) from error
    if runtime_check.returncode != 0:
        raise RuntimeError(
            "Packaged transcription runtime import check failed. "
            f"stdout: {runtime_check.stdout} stderr: {runtime_check.stderr}"
        )
    if "Packaged transcription runtime imports passed" not in runtime_check.stdout:
        raise RuntimeError(
            "Packaged backend did not report a successful transcription runtime check. "
            f"stdout: {runtime_check.stdout} stderr: {runtime_check.stderr}"
        )

    port = _free_loopback_port()
    session_token = str(uuid.uuid4())
    authorization = {"Authorization": f"Bearer {session_token}"}
    with tempfile.TemporaryDirectory(prefix="pcs-backend-smoke-") as temporary:
        work_dir = Path(temporary)
        env = os.environ.copy()
        env["PCS_DATA_DIR"] = str(work_dir / "data")
        env["PYTHONUTF8"] = "1"
        env["PCS_AI_PROVIDER"] = "gemini"
        env.pop("PCS_GEMINI_API_KEY", None)
        stdout_path = work_dir / "backend.stdout.log"
        stderr_path = work_dir / "backend.stderr.log"
        with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open(
            "w", encoding="utf-8"
        ) as stderr:
            process = subprocess.Popen(
                [str(executable), "--port", str(port), "--require-session"],
                cwd=work_dir,
                env=env,
                stdin=subprocess.PIPE,
                stdout=stdout,
                stderr=stderr,
                text=True,
                encoding="utf-8",
            )
            if process.stdin is None:
                raise RuntimeError("Packaged backend session pipe was not created.")
            process.stdin.write(f"session:{session_token}\\n")
            process.stdin.flush()
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

                # Health remains open for startup probes; application routes require the session.
                from urllib.error import HTTPError

                try:
                    _read_json(f"{base_url}/api/projects")
                except HTTPError as error:
                    if error.code != 401:
                        raise
                else:
                    raise AssertionError("Packaged API accepted a request without its session token.")

                create_request = Request(
                    f"{base_url}/api/projects",
                    data=json.dumps({
                        "name": "Packaged backend smoke test",
                        "description": "SQLite API integration check",
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json", **authorization},
                    method="POST",
                )
                with urlopen(create_request, timeout=10) as response:
                    created = json.loads(response.read().decode("utf-8"))
                if not created.get("id") or created.get("name") != "Packaged backend smoke test":
                    raise AssertionError(f"Unexpected project creation response: {created}")

                projects = _read_json(f"{base_url}/api/projects", headers=authorization)
                if not any(project.get("id") == created["id"] for project in projects):
                    raise AssertionError("Created project was not returned by the list endpoint.")

                ai_settings_url = f"{base_url}/api/settings/ai"
                credential_status = _read_json(ai_settings_url, headers=authorization)
                if not credential_status.get("credential_store_available"):
                    raise AssertionError("Packaged Windows Credential Manager backend is unavailable.")
                if credential_status.get("configured"):
                    raise AssertionError("Smoke-test Windows account unexpectedly has a pre-existing Gemini key.")

                smoke_key = "pcs-smoke-" + secrets.token_urlsafe(32)
                try:
                    saved = _request_json(
                        f"{ai_settings_url}/gemini-key",
                        "PUT",
                        {"api_key": smoke_key},
                        headers=authorization,
                    )
                    if not saved.get("configured") or saved.get("source") != "windows_credential_manager":
                        raise AssertionError("Gemini key was not stored in Windows Credential Manager.")
                    if smoke_key in json.dumps(saved):
                        raise AssertionError("Gemini key value was returned by the settings API.")
                    restored = _read_json(ai_settings_url, headers=authorization)
                    if not restored.get("configured") or restored.get("source") != "windows_credential_manager":
                        raise AssertionError("Gemini key could not be read back from Windows Credential Manager.")
                    if smoke_key in json.dumps(restored):
                        raise AssertionError("Gemini key value was exposed by the settings API.")
                finally:
                    _request_json(
                        f"{ai_settings_url}/gemini-key",
                        "DELETE",
                        headers=authorization,
                    )
                removed = _read_json(ai_settings_url, headers=authorization)
                if removed.get("configured"):
                    raise AssertionError("Gemini smoke-test key remained in Windows Credential Manager.")

                _stop_process_gracefully(process)
                if process.poll() is None:
                    raise RuntimeError("Packaged backend remained alive after graceful shutdown.")
                print("Packaged backend health, SQLite, Windows Credential Manager round-trip, and graceful shutdown tests passed.")
            finally:
                if process.poll() is None:
                    _stop_process_gracefully(process, timeout=5)
                if process.stdin is not None and not process.stdin.closed:
                    process.stdin.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
