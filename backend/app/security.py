"""Shared browser-origin policy for the local desktop API.

The service binds to loopback and has no user authentication. Browser-origin
validation is therefore an important CSRF boundary for requests initiated by
web pages in other origins. Non-browser local clients may omit Origin.
"""

ALLOWED_UI_ORIGINS = frozenset(
    {
        "http://127.0.0.1:1420",
        "http://localhost:1420",
        "http://tauri.localhost",
        "https://tauri.localhost",
        "tauri://localhost",
    }
)
