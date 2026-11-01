"""Launch the local API from a packaged desktop executable.

The bind host is deliberately fixed to loopback; only the port is configurable so
the packaged service cannot accidentally be exposed to the local network.
"""
from __future__ import annotations
import argparse

def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("port must be an integer") from error
    if not 1024 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1024 and 65535")
    return port

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Personal Clipper Studio local API")
    parser.add_argument("--port", type=_port, default=8765)
    arguments = parser.parse_args(argv)
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=arguments.port,
        log_level="info",
        access_log=False,
    )

if __name__ == "__main__":
    main()
