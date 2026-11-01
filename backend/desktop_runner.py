"""Launch the local API from a packaged desktop executable.

The bind host is deliberately fixed to loopback; only the port is configurable so
the packaged service cannot accidentally be exposed to the local network. The
desktop host requests a graceful shutdown through stdin before using a hard kill.
"""
from __future__ import annotations

import argparse
import sys
import threading
from typing import TextIO


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("port must be an integer") from error
    if not 1024 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1024 and 65535")
    return port


def _watch_shutdown(server, input_stream: TextIO | None = None) -> None:
    """Set Uvicorn's exit flag only for the explicit desktop shutdown command."""
    stream = input_stream if input_stream is not None else sys.stdin
    for line in stream:
        if line.strip().casefold() == "shutdown":
            server.should_exit = True
            return


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Personal Clipper Studio local API")
    parser.add_argument("--port", type=_port, default=8765)
    parser.add_argument(
        "--require-session",
        action="store_true",
        help="Require the Tauri parent process to provide a one-time session token on stdin.",
    )
    parser.add_argument(
        "--self-check-runtime",
        action="store_true",
        help="Import-check the bundled CPU transcription and Windows credential-store dependencies, then exit.",
    )
    arguments = parser.parse_args(argv)

    if arguments.self_check_runtime:
        try:
            import av
            import ctranslate2
            import tokenizers
            import faster_whisper
            from faster_whisper import WhisperModel  # noqa: F401
            if sys.platform == "win32":
                from keyring.backends.Windows import WinVaultKeyring

                if WinVaultKeyring.priority <= 0:
                    raise RuntimeError("Windows Credential Manager backend is unavailable.")
        except (ImportError, OSError) as error:
            raise SystemExit(
                f"Packaged transcription runtime is incomplete: {error}"
            ) from error

        print(
            "Packaged transcription runtime imports passed: "
            f"faster-whisper={getattr(faster_whisper, '__version__', 'unknown')}, "
            f"ctranslate2={getattr(ctranslate2, '__version__', 'unknown')}, "
            f"av={getattr(av, '__version__', 'unknown')}, "
            f"tokenizers={getattr(tokenizers, '__version__', 'unknown')}",
            flush=True,
        )
        return

    session_token = None
    require_session = arguments.require_session or bool(getattr(sys, "frozen", False))
    if require_session:
        first_line = sys.stdin.readline()
        if not first_line.startswith("session:"):
            raise SystemExit("The desktop session handshake is missing.")
        session_token = first_line.removeprefix("session:").strip()
        if len(session_token) != 36:
            raise SystemExit("The desktop session handshake is invalid.")

    import uvicorn
    from app.main import app, configure_session_token

    if session_token is not None:
        try:
            configure_session_token(session_token)
        except ValueError as error:
            raise SystemExit(str(error)) from error

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=arguments.port,
            log_level="info",
            access_log=False,
            timeout_graceful_shutdown=8,
        )
    )
    shutdown_watcher = threading.Thread(
        target=_watch_shutdown,
        args=(server,),
        name="pcs-shutdown-watcher",
        daemon=True,
    )
    shutdown_watcher.start()
    server.run()


if __name__ == "__main__":
    main()
