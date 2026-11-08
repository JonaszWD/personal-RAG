# Architecture

## Components

| Component | Where | Responsibility |
|---|---|---|
| Terminal UI / CLI (`tui.py`, `cli.py`) | host | Start the stack, import files (host-side copy), chat, poll indexing progress |
| Web UI (`web/`) | served by backend | Read-only mobile chat for the phone |
| Backend (`api.py`) | Docker | Auth, rate limits, audit log, endpoints, orchestration |
| Ingestion (`ingest.py`, `pdf.py`) | Docker | Scan → extract → chunk → embed → store |
| Store (`store.py`) | Docker volume | SQLite (files, chunks, float32 embeddings) + cosine search |
| Ollama | Docker, internal network | `nomic-embed-text` embeddings and `llama3.2:3b` chat |

## Data flow

**Ingest** (background job, one at a time, progress via `GET /ingest/status`)

1. `iter_safe_files` walks the read-only docs mount: regular files only, allow-listed extension, within size limit,
   no symlinks or hidden paths, capped at 200 files.
2. SHA-256 of the content; unchanged files are skipped (incremental indexing).
3. Text is extracted (UTF-8 decode, or the sandboxed PDF subprocess).
4. Text is split into 1000-char chunks with 150 overlap; chunks are embedded in batches of 16.
5. `replace_file` swaps the file's chunks atomically in SQLite. Files that disappeared are removed.
6. A failure on one file (timeout, bad PDF, DB error) is recorded and the batch continues.

**Query** (`POST /query` or `/query/stream`)

1. Embed the question, brute-force cosine similarity over all chunks, take top-4.
2. Build the prompt: system rules + each excerpt in `<excerpt source="…">` tags, declared untrusted data.
3. Generate with the local chat model (streamed as NDJSON for the web UI) and return the answer plus source files.

## Design decisions and trade-offs

- **Local-only models.** The point is privacy; a hosted API would be faster and better but would see every excerpt.
  Cost: slow answers on CPU and a small model.
- **SQLite + numpy instead of a vector database.** Fewer dependencies and a tiny attack surface; brute force is
  exact and fast enough for ≤ 200 files. The `Store` interface isolates it so an ANN index can replace it.
- **Files are copied in on the host; the container mount stays read-only.** Adding a file never widens what the
  container can see, and the validation (type, size, symlinks) runs before anything is copied.
- **PDF parsing in a resource-limited subprocess.** Parsers are a classic attack surface. Bytes go in over stdin, text
  comes out over stdout, the child has an empty environment and rlimits. A stronger boundary (separate container with no
  network) is on the roadmap.
- **Two credentials.** The full token lives on the Mac; the phone gets a read-only token exchanged for an `HttpOnly`
  session cookie, so a lost phone cannot change the index and the full token is never in a browser.
- **Indexing is an asynchronous job.** CPU embedding takes minutes. A single long request timed out and, combined with
  overlapping runs on a shared database connection, caused partial indexing (found via a real failure and fixed: job
  lock, store lock, per-file error handling, progress polling).
- **Prompt injection is treated as unsolvable in-model, so the blast radius is limited instead:** the model has no
  tools, no network and no write access; output is rendered inertly.
- **Evaluation harness over anecdotes.** `make eval` tracks hit@k, MRR, answer accuracy and abstention so changes to
  chunking or models can be compared.

## Failure handling

| Failure | Behaviour |
|---|---|
| Ollama timeout / 5xx during indexing | File marked failed, batch continues, `Ctrl+R` retries only what is missing |
| Corrupt / encrypted / oversized PDF | Skipped and reported, never crashes the backend |
| Backend down when the UI starts | The UI runs `docker compose up -d` and waits for `/health` |
| Two concurrent index requests | The second joins the running job |
| Ollama error during a streamed answer | An `error` event is sent; the page shows a message |
