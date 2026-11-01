# Security Policy

## Supported versions

This project is under active development. Security fixes are handled on the latest development branch. No stable release series is currently designated.

## Reporting a vulnerability

Please do not report exploitable vulnerabilities in a public issue or pull request.

Use GitHub's **private vulnerability reporting** feature for this repository if it is enabled. If it is not available, contact the repository owner through the contact method listed on the GitHub profile and include:

- A concise description of the issue and its potential impact.
- The affected component, version, commit, or file path.
- Reproduction steps or a proof of concept that does not expose real user data.
- Any suggested mitigation, if known.

Do not include real API keys, passwords, private videos, database files, or personal information in the report.

## Scope and security boundaries

Areas of particular interest include:

- Local API binding and origin validation.
- File upload, path handling, and media processing.
- Credential storage and secret redaction.
- Tauri command permissions and frontend/backend boundaries.
- Dependency and build pipeline integrity.

The local API is intended to bind to loopback and serve the local desktop application. It is not designed to be exposed directly to the public internet. Browser-originated requests are restricted to the desktop UI and the explicit development UI origins; requests carrying an untrusted or opaque Origin are rejected. Local native clients that omit the Origin header remain supported. This origin check is a browser CSRF boundary, not authentication, and does not make the API safe to expose beyond loopback.

## Coordinated disclosure

Please allow the maintainer reasonable time to investigate and prepare a fix before publicly disclosing a vulnerability. Do not access, modify, or delete data that is not yours while validating a report.
