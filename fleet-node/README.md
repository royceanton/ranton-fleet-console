# Private fleet node

This is the native backend for the fork's Fleet Activity section. It preserves existing Codex app authentication and uses separate owner-only profiles for Harith and Jill. No OpenAI Platform API key is needed for those native profiles.

## Prerequisites

macOS Apple Silicon, Apple Command Line Tools, Python 3.10+, Bun 1.3.14, Codex CLI and CLIProxyAPI 8.0.10 with its native plugin host. Tailscale supplies private networking. Two authorized native profiles and two proxy credential sign-ins must already exist and have matching account identities. Do not copy auth files between machines or commit them.

Runtime locations are under `~/.local/share/{codex-fleet,ranton-proxy,ranton-console}`. Runtime directories must be mode 700 and credentials mode 600. The generated management key is at `~/.local/share/ranton-proxy/management.key`; the separate gateway client key is `client.key`. The console bridge reads these privately. Keep them in your password manager if desired; never paste them into source or logs.

The initial bootstrap coordinator runs from the original bootstrap checkout. This directory is a reproducible source distribution of that coordinator, not a second simultaneously running service. To install on a fresh Mac, prepare profiles with `scripts/40_codex_accounts.py`, complete native sign-ins with `scripts/41_codex_account_session.py`, inspect observations, and install the coordinator with `scripts/43_fleet_service.py install`. The proxy service helper prepares the owner-scoped packaged gateway, but provider OAuth sign-in is a separate user action. Inspect `--help` before using helpers.

## Deployment to an existing bootstrap

From the fork root, build and test first:

```sh
bun install --frozen-lockfile
bun run verify
cd fleet-node
python3 -m unittest discover -s tests -v
cd ..
python3 fleet-node/install.py --coordinator-root /absolute/path/to/agent-fleet-bootstrap
```

The installer does not copy or upgrade coordinator source. Review and synchronize `fleet-node/fleet/` and the relevant scripts/tests into the existing coordinator checkout first, preserving local changes; otherwise new endpoints will be absent. On a fresh Mac, run the coordinator from this distribution.

The installer validates proxy/native identity matches, backs up replaced configuration, compiles `ranton-router.dylib`, enables that plugin, installs `com.ranton.console`, copies `dist/index.html`, and restarts the existing coordinator and proxy. It does not alter `~/.codex`, SSH, global Git config, shell profiles, Tailscale identity or provider OAuth files. Its output prints backup paths, never credentials.

Verify these before changing private Serve routes:

- `http://127.0.0.1:8318/health` is healthy.
- Authenticated `/v8/management/plugins` lists `ranton-router` with `registered: true` and `effective_enabled: true`.
- Authenticated `http://127.0.0.1:8318/v8/management/fleet/status` shows both account observations.
- Unauthorized management requests return 401; hostile Host/Origin values return 403.
- A stable `session_id` request reaches the selected account; a missing ID returns 503.

Services: `com.ranton.console` (:8318), `com.ranton.proxy` (:8317), `com.ranton.fleet` (:8765), all listening on loopback. Set the console's `PRIVATE_HOST` and coordinator's corresponding allowlist to the chosen private DNS hostname when moving to a different host; take a configuration backup first. The current deployment uses `macbook-pro.tail9ad173.ts.net:8443`.

After verification, publish only the selected ports with Tailscale Serve. Preserve unrelated routes. Do not use Funnel. The inference route should bypass the console bridge so native SSE and WebSocket behavior stays intact.

## Policy and controls

New independent tasks choose their account automatically. Every reported quota window must retain the reserve, default 10%. You can adjust the reserve to 5–50% and native concurrency to one or two slots in the Routing dialog under Fleet jobs. Prioritize useful work; empty queues remain idle. Gateway clients must attach a stable `session_id`; this is separate from native task sessions.

From a native Codex control chat signed into Harith, a host agent can submit a separate worker using Jill's ChatGPT subscription:

```sh
python3 scripts/42_fleet.py submit PROJECT_ID 'Jill read-only test' --account jill --goal-file /absolute/goal.txt
python3 scripts/42_fleet.py task TASK_ID
```

The optional `--account` constraint is serialized as `requested_account` on the authenticated task API. It waits if the requested account fails quota, model, freshness or occupancy checks, rather than using the other account. Existing task/session bindings take precedence during continuation. Omit the option for normal automatic routing. The original control chat still uses its signed-in subscription; only the delegated worker uses the requested profile. Worker account, native session and provider-reported usage supply the execution evidence. The job appears under **Fleet activity → Fleet jobs**, with its control-chat association, without changing the app login or consuming reset credits.

Pause new work stops new task dispatch, not already running tasks or the gateway. Pause a specific thread to stop its worker while retaining its workspace/session. Explicitly resume interrupted work after a restart. Follow-ups keep the same account and session. Review results before integrating their worktree changes. Never run two coordinators against the same runtime directory; an exclusive lock enforces that rule.

The legacy native `web/` UI is retained as an internal recovery surface; the user-facing control surface is the forked CPAMC Fleet Activity page.

## Codex observation

`GET /v8/management/fleet/codex` and `/codex/chats/<uuid>` use the existing management authentication and bridge allowlist. The coordinator exposes equivalent authenticated `/api/codex` routes. No mutation route is added for app chats.

The local connector opens the owner’s Codex stores read-only, selects metadata and redacted commands, and never repairs/imports local history. It checks canonical IDs, ownership and symlink paths, bounds records, and caches snapshots briefly in memory. Projects come from actual sidebar/database records, matched by exact roots; parent relationships come from stored agent metadata. Working means an unfinished saved turn with a held writer lock. Legacy chats without supported operation history are explicitly unknown. Incomplete metadata reads are retried briefly. Failed refreshes retain the last verified records in memory, explicitly stale with unknown live state and their original observation time. No prompt is required to refresh. Cold starts without verified data stay unavailable; unsafe ownership/path checks clear cached data. Unsupported schema is reported visibly, and fleet jobs are never substitute data. Individual history reads handle database locks and report stale evidence. Logs contain error categories only, not parser/exception text.

This internal schema was inspected against Codex CLI 0.160.0 and needs rechecking on upgrades. Messages, reasoning, prompt previews, command output, tool arguments, auth/account IDs and credentials are excluded. No extra agent process, provider inference, remote-host access or database writes are used to observe Codex. The ordinary app’s inference does not pass through this router merely because its activity is visible.

## Rollback

1. Pause new dispatch and wait for or explicitly pause active native tasks.
2. Restore the timestamped `config.yaml-before-*` backup printed by the installer to `~/.local/share/ranton-proxy/config.yaml`, keeping mode 600; restart `com.ranton.proxy`. This returns gateway routing to its stock policy.
3. Restore only the private console route: `tailscale serve --bg --https=8443 http://127.0.0.1:8317`. Remove the added inference route with `tailscale serve --https=8444 off` if no longer needed. Do not reset the entire Serve configuration.
4. Unload the new console service with `launchctl bootout gui/$(id -u)/com.ranton.console`. Retain its source and backups for recovery.

The database migrations only add journal/binding tables and retain all task, project, profile and result data. Disabling the fork does not delete them. To roll back coordinator code, check out the previous source revision after stopping the service; retain the database and require explicit resume of interrupted tasks.
