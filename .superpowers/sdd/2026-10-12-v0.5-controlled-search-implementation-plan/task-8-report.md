# Task 8 — Deployment documentation, packaging, and release acceptance

## Delivered

- Added Chinese-first [search operations guidance](../../../../docs/search-operations.md) covering optional SearXNG setup, the pinned image tag, enabled engines and caveats, JSON/SafeSearch behavior, source review, group/deployment policies, classifier and model data flow/cost, local/auto/online meme modes, status troubleshooting, logging, backup, rollback, and unverified field acceptance.
- Updated README, architecture, verification record, and changelog for v0.5 behavior and `0.5.0` release metadata.
- Added only safe placeholders and bounded SearXNG origin/client settings to `.env.example`; no real `.env` or database was accessed. Ignored the generated `deploy/searxng/settings.yml` file so its secret is not accidentally committed.
- Set package/CLI version to `0.5.0`, updated the lock and CLI/metadata assertions, and extended wheel distribution coverage to the bundled default prompt.
- Prevented the shared Compose env file from passing `SEARXNG_SECRET` into the bot container; a test asserts that the bot gets an empty value while the SearXNG service gets the configured interpolation. Existing project name and named volumes remain unchanged; search has no published port.

## Verification

- `uv run --frozen --extra dev pytest tests/test_cli.py tests/webui/test_distribution.py -q` — **9 passed**, existing Starlette/httpx deprecation warning.
- `uv run --frozen --extra dev pytest -q` — **544 passed**, same existing warning. One stale `test_rename.py` version assertion was updated from 0.4.0 to 0.5.0 before the final full run.
- `uv run --frozen --extra dev pytest tests/test_compose.py -q` — **2 passed** after secret-isolation assertions were added.
- `uv run --frozen --extra dev ruff check .`, `uv run --frozen --extra dev ruff format --check .`, `uv lock --check` — passed.
- `uv build` — produced `aling-0.5.0.tar.gz` and `aling-0.5.0-py3-none-any.whl`; `uv run --frozen aling --version` — `ALING 0.5.0`.
- Both required Compose `config --quiet` commands passed using `.env.example`. Parsed base and merged configs confirmed project `ryxbot`, legacy `bot-data`/`napcat-qq`/`napcat-config` volumes, no SearXNG host port, shared bot/search network, and secret only in the search service.
- Docker Engine was available; `docker build -t aling:0.5.0-task8-check .` passed. No container or external service was started.
- `git diff --check` passed.

## Boundaries and review handoff

- The SearXNG container and its runtime/access logs were not exercised. Live engine availability, search quality, model provider handling/cost, real image availability, and QQ delivery remain owner-controlled field checks.
- No push, tag, deploy, NapCat login, QQ message, real `.env`, live database, or account was touched.
- Task 8 implementation is committed; the requested fresh whole-branch read-only review is the next gate.

## Commit

- `chore: prepare ALING v0.5 release`
