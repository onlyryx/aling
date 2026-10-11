# Task 5 implementation report

Status: IMPLEMENTED (automated verification complete; independent review and browser acceptance remain with controller)

Worktree: `/Users/jack233/.codex/worktrees/aling-v03-upgrade/ryxbot`
Branch: `codex/aling-v04-webui`
Base: `0ec0e6b655ac267a7c714e5ed35d8454a43a1f32`
Commit: this report and implementation are included in `feat: add panel schedule management`.

## Implemented

Added `/api/admin/schedules`, `PUT/DELETE /api/admin/groups/{id}/schedule` and `PUT /api/admin/groups/{id}/proactive`. Writes use the existing scheduler and proactive sender with a server-built `PermissionContext`; strict request models reject extra identity/role fields. Time is bounded to 00:00–23:59 and text to 1–500 nonblank characters. Storage and permission errors return fixed responses.

The read view includes only groups in configured scope, saved plans and last attempted date, configured timezone and quiet hours, the global deployment switch, hourly/daily limits and cooldown. Each plan exposes truthful inactive reasons for the global switch, group switch, allow-list scope, and OneBot connection. No send-now route or delivery-success claim was added. Delete/recreate continues to rely on the existing persistent schedule claim table.

Added the responsive schedule page with per-group time/message editing, deletion and proactive toggle. Failed requests leave current form values intact. Status shows global/group blockers and the last attempt date; schedule writes only persist configuration and never trigger a send.

## Test-first and verification

The initial `uv run --frozen --extra dev pytest tests/webui/test_schedules.py -q` run failed on the expected missing schedule routes (6 failures). After implementation, the focused suite passed. Tests cover both legal boundary times, invalid 24:00/empty/501-character input, valid 500-character input, forged identity, disallowed groups, global proactive disable with no send, and deleting/recreating a plan after a fictional same-day claim. The fixture calls the existing scheduler with a fixed aware clock and an in-memory send recorder.

`uv run --frozen --extra dev pytest tests/webui/test_schedules.py tests/test_scheduler.py tests/test_integration.py -q`: 18 passed, 1 existing Starlette TestClient/httpx deprecation warning.

`uv run --frozen --extra dev pytest -q`: 305 passed, 1 existing Starlette TestClient/httpx deprecation warning.

`uv run --frozen --extra dev ruff check .`: passed.

`uv run --frozen --extra dev ruff format --check .`: 85 files already formatted.

`node --input-type=module --check < src/aling/webui/static/pages/schedules.js`: passed.

`git diff --check`: passed.

The schedule module was retrieved through the authenticated test app. I did not run interactive browser acceptance; it remains for the controller's review. No real QQ/NapCat login, credential, external service or deployment was used.

## Fix round 1 — restore schedule deletion after proactive save

Controller browser acceptance found that saving a group's proactive switch left an existing schedule's Delete button disabled until manual refresh. The proactive save's `finally` block now restores that button from the refreshed `hasSchedule` state, matching schedule-save cleanup.

Added `tests/webui/fixtures/schedules_save_state.mjs` and a Python runner. The fixture executes the real page module with a small DOM adapter: it first checks that a failed schedule save and a subsequent refresh preserve the unsaved time/message, then saves the proactive switch and asserts the existing plan's Delete button remains enabled.

RED: `node tests/webui/fixtures/schedules_save_state.mjs src/aling/webui/static/pages/schedules.js` failed at the regression assertion (`true !== false`) before the fix. GREEN: the same command passed after the fix and reported that drafts remain preserved and Delete remains enabled.

Focused verification: `uv run --frozen --extra dev pytest tests/webui/test_schedules.py tests/test_scheduler.py tests/test_integration.py -q` — 19 passed, 1 existing Starlette TestClient/httpx deprecation warning. Ruff check passed; Ruff format check reported 85 files already formatted; schedule page module syntax check and `git diff --check` passed.
