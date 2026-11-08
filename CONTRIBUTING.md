# Contributing

```bash
make install   # dependencies + git hooks
make check     # ruff, mypy --strict, pytest (coverage gate ≥ 80%)
```

- Python 3.12, managed with [uv](https://docs.astral.sh/uv/). Keep `uv.lock` committed.
- Code must pass `mypy --strict` and ruff (including the `S` security rules). Add a test with every change; security-relevant
  behaviour (auth, path checks, parsing) needs a negative test.
- Commits use [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:` ...).
- **Never commit personal data.** Your documents live in `documents/` (git-ignored). Examples and tests must use the
  synthetic corpus in `sample_docs/`. A pre-commit hook and CI (gitleaks) back this up.
- Security issues: see [SECURITY.md](SECURITY.md).
