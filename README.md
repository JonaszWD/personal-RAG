# Personal RAG (proof of concept)

Ask questions about your own files. **Everything runs locally** — no cloud APIs, no telemetry.

- Backend: Python 3.12, FastAPI, SQLite, Ollama (embeddings + chat) — in Docker
- Frontend: terminal CLI (`personal-rag`, runs on host)
- Tooling: uv, ruff, mypy, pytest, GitHub Actions

## Scope (deliberately small)

`.txt`/`.md` (≤1 MB) and text-based `.pdf` (≤10 MB, first 100 pages), ≤200 files, from **one** folder. No Office, no OCR (scanned PDFs yield no text), no encrypted PDFs.

## Quick start

```bash
echo "RAG_API_TOKEN=$(openssl rand -hex 32)" > .env
# optional: RAG_DOCS_HOST_DIR=/path/to/notes in .env (default ./sample_docs)
docker compose --profile setup run --rm pull-models   # one-time model download
docker compose up -d --build
set -a; source .env; set +a
uv run personal-rag ingest          # index
uv run personal-rag ask "question"  # one-shot
uv run personal-rag                 # interactive (/ingest, /quit)
```

Dev: `uv sync --group dev && uv run pytest && uv run ruff check . && uv run mypy`

## Security model

| Threat | Mitigation |
|---|---|
| Data exfiltration | Local models only. Ollama sits on an `internal: true` network with no internet. |
| Other apps/LAN reaching API | Published on `127.0.0.1` only; bearer token (≥32 chars, constant-time compare). |
| Reading files outside scope | Single `:ro` mount; symlinks rejected; hidden files skipped; extension allowlist; size/count caps. |
| Container compromise | Non-root, read-only rootfs, `cap_drop: ALL`, `no-new-privileges`, pid/memory limits. |
| Prompt injection from documents | Excerpts delimited and labelled untrusted; model has no tools, so worst case is a bad answer. |
| Terminal injection | CLI strips control/escape characters from all backend text before printing. |
| Malicious PDFs | Parsed (pypdf, pure Python, no JS execution) in a child process: bytes via stdin, empty env, CPU/memory/file rlimits, timeout, page + output caps, magic-byte check, encrypted PDFs skipped. |
| Leaks via logs | Only file names/counts logged, never content or questions. |
| Supply chain | `uv.lock` frozen, Dependabot, `pip-audit`, Trivy image scan, gitleaks in CI. |

Known limits: the `backend` container shares a bridge with the host for the published port, so it has egress (it doesn't need it; tighten with a firewall/proxy later). Token is passed via env var. Index at rest is unencrypted in the Docker volume — use FileVault.

## CI/CD

`.github/workflows/ci.yml`: lint, format, types, tests, dependency audit → Docker build + Trivy scan → push to GHCR on `v*` tags.
