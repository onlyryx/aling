# Task 3 report — bounded SearXNG search

## Delivered

- Added monotonic task budgets with the 60-second lifetime, 57-second work cutoff, reserved delivery window, model/tool/external-operation counters, and bounded per-user/group/instance admission.
- Added a fixed-origin SearXNG client that sends form-encoded `POST /search`, disables environment proxies and redirects, clips timeouts to task budget, bounds response bytes/connections, strictly parses the JSON result shape, and emits only stable error codes. The raw query is not in the URL or app logs.
- Added the `/search <keywords>` QQ plugin with policy/classifier/source gates, safe Chinese error mapping, up to three approved result links/snippets, and deadline-limited delivery.
- Added opt-in `search` Compose profile with a pinned SearXNG image tag, no published port, explicit Granian access-log disablement, isolated search cache, fixed engine/safesearch/JSON settings, and secret interpolation through `$SEARXNG_SECRET`.
- Added minimal `Runtime` ownership/injection/cleanup for the search client and service. This is the narrow architecture-ownership extension approved by the parent; no Task 4 scheduler/chat orchestration was added.
- Added budget/client/Compose tests and an end-to-end fake OneBot WebSocket `/search` test. The integration test injects a fake classifier and fake transport; it does not log into QQ or contact public search engines.

## Files

- `src/aling/services/search_budget.py`
- `src/aling/services/search.py`
- `src/aling/plugins/search.py`
- `src/aling/core/config.py`
- `src/aling/app.py`
- `src/aling/runtime.py`
- `src/aling/services/search_policy.py`
- `compose.search.yaml`
- `deploy/searxng/settings.yml.example`
- `tests/test_search_budget.py`
- `tests/test_search.py`
- `tests/test_compose.py`
- `tests/test_integration.py`

## Verification

- `uv run --frozen --extra dev pytest -q` — **483 passed**, one existing Starlette/httpx deprecation warning.
- `uv run ruff check src/aling tests` — passed.
- `uv run ruff format --check src/aling tests` — passed; 77 files formatted.
- `SEARXNG_SECRET=local-validation-only-32-characters-long RYXBOT_ENV_FILE=.env.example docker compose --env-file .env.example -f compose.yaml -f compose.search.yaml --profile search config --quiet` — passed.
- `git diff --check` — passed.

## Known gap / ruling

- **Ruling:** `src/aling/runtime.py` was added to the task diff solely for client/service ownership, transport injection, and shutdown cleanup, as approved by the parent. The search classifier remains unconfigured by default and the policy gate fails closed; Task 4 owns scheduler-backed classifier wiring.
- The SearXNG container was not started. Therefore the optional image's startup behavior and its actual container logs were not exercised. Compose validation confirms there is no host port and access logging is disabled; the fake integration verifies the bot's app logs omit its test query. Before enabling the profile, the deployer must copy `deploy/searxng/settings.yml.example` to `deploy/searxng/settings.yml` and provide a locally generated `SEARXNG_SECRET`; Task 8 owns deployment documentation.
- No real `.env`, QQ account, persistent database, or external deployment was accessed or modified.

## Commit

- `feat: add bounded SearXNG search`
