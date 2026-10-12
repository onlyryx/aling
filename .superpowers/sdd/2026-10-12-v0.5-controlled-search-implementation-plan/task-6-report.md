# Task 6 — Owner Policy API, Diagnostics, and Blue WebUI

## Implemented

- Added authenticated strict owner APIs for deployment search policy, group-level further restrictions, consent acknowledgement, safe search status, and a fixed-query probe. Input models reject extra fields, loose booleans/integers, invalid purpose/mode values, and owner/role spoofing. Group policy reads and writes are limited to configured groups.
- Added the persisted owner-only AI-tools read/write API. It uses the authenticated panel session owner ID and the shared SQLite setting consumed by runtime paths.
- Wired policy changes and disabling/tightening feature changes through revision publication, in-flight task cancellation, and awaited temporary online-meme revocation before reporting success. Disabling consent similarly cancels active tasks and expires temporary resources.
- Added a safe probe using the authenticated panel session, selected allowed group, normal policy/classifier/quotas, fixed origin client, fixed nonsensitive query, and result validator. Probe/status payloads contain only fixed categories, booleans/unknowns, count, timestamp, and safe code; query text, results, and upstream URLs are never returned or recorded.
- Added the fifth “联网设置” page with global and per-group controls, source/purpose/limit editing, dependency blockers, explicit one-time data-flow consent, AI-tools control, probe, and operational status. Overview links to safe status. Static assets remain local and are included in the installed wheel.

## Rulings

- **Ruling:** Added authenticated `/api/admin/groups/{group_id}/search-policy` GET/PUT routes because Task 6 requires an owner UI for group-level narrowing but does not specify route names. These endpoints only accept configured groups and persist restrictions within the deployment policy ceilings.
- Probe status reports reachability and JSON support as unknown when a check is stopped by budget or quota before the SearXNG request; it reports an upstream failure separately from service unreachability.

## Verification

- Plan’s initial red-stage command `uv run pytest tests/webui/test_search.py tests/webui/test_status.py -q` produced the expected missing-route failures before implementation.
- `uv run --frozen --extra dev pytest tests/webui/test_search.py tests/webui/test_status.py -q` → **23 passed**, one existing Starlette/httpx deprecation warning.
- `uv run --frozen --extra dev pytest tests/webui/test_search.py tests/webui/test_features.py tests/webui/test_status.py tests/webui/test_distribution.py tests/webui/test_ai_tools.py -q` → **33 passed**, one existing Starlette/httpx deprecation warning.
- `uv run --frozen --extra dev pytest tests/test_features.py tests/test_search_policy.py tests/test_online_memes.py -q` → **168 passed**.
- Ruff check and format passed for changed Python files; `node --check` passed for the search page and app entry point; `git diff --check` passed.
- No live `.env`, QQ account, production database, deployment, or push was used.

## Review boundary

Task 6 is ready for its independent review. Task 7 cross-entry integration and help/privacy regression work remains unstarted.
