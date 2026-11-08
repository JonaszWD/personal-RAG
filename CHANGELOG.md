# Changelog

## 0.1.0 - 2026-11-08

First release.

### Added
- Local RAG pipeline: chunking, Ollama embeddings, SQLite index, cosine retrieval, grounded answers with sources.
- Text, Markdown and sandboxed PDF ingestion with incremental indexing.
- Terminal UI (Textual): multi-select file/folder picker, drag-and-drop import, live indexing progress; scriptable CLI.
- Read-only mobile web UI over Tailscale: separate remote token, session cookie, CSRF/Host protection, rate limits, CSP.
- Evaluation harness (`make eval`) with a synthetic corpus and labelled questions.
- CI: lint, format, strict typing, tests with coverage, dependency audit, Docker build, Trivy and gitleaks scans.

### Fixed
- Indexing aborted after the first failure and overlapping index requests corrupted shared state; indexing is now a
  background job with per-file error handling, a store lock and progress reporting.
- Ctrl+A in the question box was shadowed by the text input's own binding.
