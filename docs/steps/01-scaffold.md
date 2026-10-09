# Step 01: Project Scaffolding and Environment Setup

## Objective

Scaffold the project, pin Python to 3.12, record the core architectural decisions in ADR 0001, configure fail-fast settings, and establish verification tooling.

## Scope

- Python 3.12 virtual environment via `uv`.
- Minimal dependency manifests (`requirements.txt`, `requirements-dev.txt`).
- Environment variable template (`.env.example`).
- Relocate canonical goal document to `docs/GOAL.md`.
- Architecture Decision Record (`docs/adr/0001-architecture.md`).
- Strongly-typed, fail-fast configuration (`config.py`).
- Unit tests for configuration validation (`tests/test_config.py`).
- Verification script (`scripts/verify.ps1`, `scripts/check-env.ps1`).
- Domain context in `CONTEXT.md` and `README.md`.

## Plan

1. Pin Python 3.12 with `uv venv --python 3.12 .venv`.
2. Author `requirements.txt` and `requirements-dev.txt` without `respx`.
3. Create `.env.example` with complete configuration keys.
4. Update `.gitignore` with `data/`, `tmp/`, `*.part`, and SQLite sidecars.
5. Move `GOAL.md` to `docs/GOAL.md`.
6. Record ADR 0001 in `docs/adr/0001-architecture.md`.
7. Implement `config.py` with typed, fail-fast validations.
8. Implement `tests/test_config.py` and run via pytest.
9. Wire `scripts/verify.ps1` to pytest and `scripts/check-env.ps1` for Python 3.12 and port 5001.
10. Update `CONTEXT.md` and `README.md`.

## Implementation

- Successfully created `.venv` running CPython 3.12.4 via `uv`.
- Split requirements cleanly to keep runtime dependencies minimal (only `python-telegram-bot`, `httpx`, `python-dotenv`).
- Structured `config.py` to parse `TELEGRAM_ALLOWED_USER_IDS` into `frozenset[int]` and fail fast if empty.
- Updated verification scripts and confirmed 9 passing tests.

## Discoveries

- Local machine has both Python 3.14.3 and Python 3.12.4 installed. Python 3.12.4 was located and pinned via `uv`.
- IPFS / Kubo daemon is not currently listening on port 5001.

## Verification

- Tests run: 9 passed, 0 failed (`tests/test_config.py`).
- Verification script: `powershell -ExecutionPolicy Bypass -File scripts/verify.ps1` passed.
- Git status: Clean commit `f0fe965`.
- Push: Successfully pushed to `origin/master` on GitHub.

## Diff / Checkpoint

- Commit: `f0fe965` (`chore: scaffold project, pin python 3.12, record architecture decision`).
- Remote: Pushed to `https://github.com/richmondsogo/Nabu-V1.git`.

## Unresolved Issues

- Local Kubo daemon is not installed / running on port 5001.

## Decisions

- ADR 0001 recorded and accepted.
