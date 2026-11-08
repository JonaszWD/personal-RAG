# Security

Personal RAG indexes private files, so it is designed assuming that documents, the network and parts of the stack can
be hostile. This file records the threat model, what is mitigated, and what is not.

## Reporting a vulnerability

Please use GitHub's **private vulnerability reporting** (Security tab → "Report a vulnerability") rather than a public
issue. This is a personal proof of concept; I will respond on a best-effort basis.

## Assets and trust boundaries

- **Assets:** document contents, the index (embeddings + text chunks), API tokens.
- **Trusted:** the user's Mac and the local Docker daemon.
- **Untrusted:** document *contents* (may carry prompt injection or malformed PDFs), anything on the network, a lost
  phone, and (partially) the model's output.

## Threat model

| # | Threat | Mitigation | Residual risk |
|---|---|---|---|
| 1 | Document data leaves the machine | Local-only models; Ollama on an `internal: true` network; no telemetry | Backend container shares a bridge for its published port (has unneeded egress) |
| 2 | Other local apps / LAN reach the API | Port bound to `127.0.0.1`; bearer token ≥ 32 chars, constant-time comparison | Any local process of the same user can read `.env` (0600) |
| 3 | Access to files outside the intended folder | One `:ro` bind mount; symlinks rejected; hidden files skipped; extension allow-list; size and count caps; folder imports re-check every file | — |
| 4 | Malicious or malformed PDF | Magic-byte check; `pypdf` (pure Python, no JS) in a child process with empty env, stdin-only input, CPU / memory / file-size rlimits, wall-clock timeout, page and output caps; encrypted PDFs skipped | Parser bugs in `pypdf`; rlimit memory cap is Linux-only (applies in Docker) |
| 5 | Prompt injection via document text | Excerpts wrapped in tags and declared untrusted; closing-tag stripping; the model has **no tools or network**, so the worst case is a wrong or misleading answer | A hostile document can still influence the answer text |
| 6 | Terminal escape / markup injection | Backend text rendered as plain `Text` (no markup); control characters stripped in CLI and TUI | — |
| 7 | Web UI XSS | Strict CSP (`default-src 'none'`, scripts/styles `'self'`), no inline JS/CSS, UI writes with `textContent` only | — |
| 8 | Stolen or lost phone | Separate **read-only** remote token (cannot index); `HttpOnly; Secure; SameSite=Strict` `__Host-` cookie; 12 h sessions in memory; revoke by rotating `RAG_REMOTE_TOKEN` | Index contents are readable until the token is rotated |
| 9 | CSRF / DNS rebinding | Origin must match Host on cookie-authenticated writes and login; `Host` allow-list; no CORS | — |
| 10 | Credential guessing | Login lockout (5 failures / 10 min), query rate limit, tailnet-only reachability | Lockout is global (all requests arrive via the proxy), so a tailnet peer could lock phone login for 10 min |
| 11 | Container compromise | Non-root user, read-only root filesystem, `cap_drop: ALL`, `no-new-privileges`, pid and memory limits | Docker daemon / kernel escapes are out of scope |
| 12 | Sensitive data in logs | Audit log has method, path, status, scope and latency only; never questions, answers or tokens (covered by a test) | — |
| 13 | Vulnerable dependencies | Locked with `uv.lock`; Dependabot; `pip-audit` and Trivy in CI | Zero-days |
| 14 | Secrets or personal files committed | `.gitignore`, a pre-commit guard, gitleaks in CI; sample data is synthetic | — |

## Known limitations

- The index is stored unencrypted in a Docker volume — rely on full-disk encryption (FileVault).
- Tokens are passed to the container via environment variables.
- Loopback traffic is plain HTTP; the phone path is HTTPS via `tailscale serve`.
- Sessions are in memory: restarting the backend logs every browser out (intentional).
- No per-document access control: anyone holding a valid token can query everything indexed.

## Hardening ideas (not yet done)

- Egress proxy / firewall for the backend container; Docker secrets instead of env vars.
- Separate containers for PDF parsing and OCR with no shared volumes.
- Pin GitHub Actions by commit SHA; sign images (cosign) and generate an SBOM.
- Encrypt the index at rest.
