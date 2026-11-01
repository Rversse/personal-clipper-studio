import json
from pathlib import Path


def test_tauri_content_security_policy_has_explicit_restrictions():
    repository_root = Path(__file__).resolve().parents[2]
    config_path = repository_root / "src-tauri" / "tauri.conf.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))

    policy = config["app"]["security"]["csp"]
    directives = {
        directive.split()[0]: directive.split()[1:]
        for directive in policy.split(";")
        if directive.strip()
    }

    assert directives["default-src"] == ["'self'"]
    assert directives["base-uri"] == ["'self'"]
    assert directives["object-src"] == ["'none'"]
    assert directives["frame-ancestors"] == ["'none'"]
    assert directives["form-action"] == ["'self'"]
    assert "http://127.0.0.1:8765" in directives["connect-src"]
    assert "http://localhost:8765" in directives["connect-src"]

    # Keep script execution restricted to packaged application resources.
    assert "'unsafe-inline'" not in directives.get("script-src", [])
    assert "'unsafe-eval'" not in directives.get("script-src", [])
