# Ranton fleet console

This fork adds **Observe → Fleet activity** to the existing CLI Proxy API Management Center. It uses the upstream navigation, components, authentication, themes and quota pages. The extension connects a private native Codex coordinator to the packaged CLIProxyAPI gateway.

## What the audit found

Audit baselines: CPAMC `ee79a794526a30c03748a8864a9ac6589a31833b`; CLIProxyAPI `8ef43e4df3b216a42493105d31c2873b69191473`. The deployed gateway is Homebrew CLIProxyAPI 8.0.10.

| Capability | Upstream | This fork |
| --- | --- | --- |
| Credentials, provider quotas, request counts and logs | Existing management pages and v8 APIs | Reused |
| Session affinity and parent/subagent affinity | Existing gateway support; normal affinity permits failover | Durable coordinator bindings; unavailable bound accounts wait |
| Scheduler extension interface | Native C ABI and scheduler capability | Small macOS plugin, using the packaged gateway |
| Repository task queue and native agent supervision | Outside CPAMC's management role | Persistent queue, bounded workers and isolated Git worktrees |
| Actual Codex projects, chats and agent relationships | No local Codex app observation in the audited UI | Read-only Codex view using actual metadata, with command/tool lifecycle evidence |
| Submitted repository jobs and review | No shared repository-task journal | Separate Fleet jobs view with automatic routing, task provenance and focused results |
| Reset-aware, task-level quota admission | Stock selectors do not implement this fleet policy | One coordinator checks all reported windows, freshness, health and account occupancy |
| Cross-account computation-cache transfer | Not established | Never claimed or attempted |

Relevant upstream sources: `src/components/layout/MainLayout.tsx`, `src/pages/DashboardPage.tsx`, `src/services/api/client.ts`; CLIProxyAPI `internal/api/server_management_v8.go`, `internal/pluginhost/{host,rpc_client,loader_unix}.go`, `sdk/pluginapi/types.go`, `sdk/cliproxy/auth/conductor_selection.go`.

The intended experience comes from [Theo's video](https://www.youtube.com/watch?v=D8PikZ1KhUo): reset preference around 18:04–20:21 and keeping a thread on its account around 20:51–21:25. The recovered transcript is an unverified third-party mirror, not an independently verified recording. See [source provenance](fleet-node/SOURCE_NOTES.md). Claude-specific ratios and cache lifetimes were not applied to Codex subscriptions.

## Decisions after self-review

The owner explicitly requested autonomous self-grilling while away. These decisions resolve the main design questions without an interactive interview:

1. **What does “threads” mean?** The user clarified that the default view must observe their actual Codex projects and conversations. Codex observation uses real local metadata; fleet-managed tasks remain a separate Fleet jobs source. Other apps and arbitrary Mac processes are outside this version. “Threats” was interpreted as “threads”; this is not a security-threat detector.
2. **Who owns routing?** The persistent coordinator. The UI configures and explains policy; the gateway plugin submits filtered, available credential candidates. There is no second independent quota algorithm.
3. **What is an eligible account?** A verified identity, fresh observations (at most five minutes), healthy authentication, no active native task on that account, and every reported quota window above the configured reserve. Missing quota data blocks admission. Gateway model entitlement is filtered upstream before scheduling.
4. **Which allowance is used?** Independent new sessions/tasks prefer the eligible account with the earliest reset. Task priority governs queue order. Existing sessions retain their account, model and workspace. A depleted or unavailable bound account pauses rather than silently switching.
5. **How is context retained?** Resume the same account's native session. Persist goal, revision, branch, result, tests and remaining work in a local checkpoint. Parent gateway sessions inherit bindings when the host supplies a parent identity. Provider-side computation cache stays account-specific; prefix reuse is an efficiency strategy, not a guaranteed cache hit.
6. **What is recorded?** Native command text after redaction, file paths, tool-operation labels, exit status and lifecycle times. Gateway records model, hashed correlation identifiers and completion/status. No command stdout, prompts, response bodies, provider errors, OAuth contents or API-key dictionary keys enter the journal.
7. **What can unattended tasks do?** Read-only analysis or edits in an opted-in isolated Git worktree. Network access is disabled for native task tools. No automatic merge, push, deployment or quota-credit reset. Native workers have a 20-minute default bound, at most two slots, and one task per account.
8. **What is “24/7”?** LaunchAgents restart services while the Mac is awake, logged in and online. No work is fabricated to burn tokens. The second Mac is a later worker-registration milestone; this implementation operates the 24 GB Mac.

## Components and access

```text
Private browser / iPhone over Tailscale
  → console bridge :8318 → upstream management API :8317
                        → native coordinator :8765 → isolated Codex workers
Gateway clients → CLIProxyAPI :8317 → scheduler plugin → same coordinator policy
```

The bridge serves the compiled, single-file React console and adds only `/v8/management/fleet/*`. It forwards ordinary v8 management calls. Inference and WebSocket traffic go directly to CLIProxyAPI; the bridge does not reimplement either protocol.

On the initial Mac:

- Console: `https://macbook-pro.tail9ad173.ts.net:8443/management.html#/fleet`
- Gateway base: `https://macbook-pro.tail9ad173.ts.net:8444/v1`
- Both are private Tailscale Serve routes. Funnel is disabled. The pre-existing port 443 route is preserved.
- Use the existing proxy management key for the console. Use the distinct client key for inference. Neither key is included in this repository.
- Each gateway conversation must send a **stable `session_id` header on every request**. Clients must reuse it across tool turns. Requests without it fail with HTTP 503. New conversation, new ID. The plugin does not infer conversation identity from prompt content.
- Tasks submitted under Fleet jobs use native Codex profiles. Ordinary Codex app chats are not automatically redirected through this proxy.

`gateway-accounts.json` is generated outside Git only after comparing proxy account-ID hashes to the native profile identities. It contains routing identifiers and fingerprints, never OAuth tokens. Bindings and task state are in the private coordinator SQLite database.

Gateway credential counters are upstream process counters and reset on proxy restart. The UI labels that scope. The operations journal and session bindings survive restarts. The destructive upstream usage queue is not consumed by this extension.

## Build, test and deploy

See [fleet-node/README.md](fleet-node/README.md) for prerequisites, installation, authentication boundaries and rollback. Main commands:

```sh
bun install --frozen-lockfile
bun run verify
cd fleet-node
python3 -m unittest discover -s tests -v
```

The native plugin builds with Apple Clang and Foundation; installing Go or replacing the Homebrew gateway is unnecessary. Keep the upstream MIT license and rebase upstream updates onto this fork. `fleet-node/` contains the complete native coordinator source, service helpers and tests, without credentials or runtime data.

## Read-only Codex connector

The observed source reads the desktop project/sidebar metadata, `state_5.sqlite` and `thread_history_1.sqlite` using explicit projections and read-only connections. Membership uses real IDs and root paths; agent children inherit an ancestor project where necessary. No prompt previews, messages, reasoning, outputs, tool arguments, credentials or account IDs are exported. Working requires a saved unfinished turn plus an active writer. Missing history stays unknown. Temporary read failures retain last verified local records in memory with a stale notice and unknown live state; a successful refresh recovers without a chat prompt. Unsafe path/ownership failures clear cached data. Cold starts and unsupported schemas remain visibly unavailable or show explicitly stale verified records; setup jobs are never fallback data.

This internal local contract was inspected with Codex CLI 0.160.0. It is version dependent and must be rechecked on updates. The supported app-server thread/turn/item APIs were reviewed, but no attachable read-only endpoint for the existing app process was available on this Mac. Starting a separate process would not establish authoritative status for the existing process, so the connector does not do that. [Official app-server documentation](https://learn.chatgpt.com/docs/app-server) describes those APIs. Remote/cloud hosts and other apps are not covered.

## Current limits

The implementation has one registered execution host. It does not yet offer arbitrary task DAGs, autonomous child-agent creation, seamless cross-account handover, remote filesystem synchronization, push notifications, or a policy editor for every gateway client. Existing quota pages remain available. A native task's resource controls and writer isolation do not apply to arbitrary third-party clients doing their own tool execution.

Account identity refresh, quota availability and provider rules remain external conditions. Missing data causes a visible wait. If the coordinator becomes unavailable, the active gateway plugin rejects admission instead of falling back to a different credential. Disabling the plugin returns the gateway to its stock routing behavior; it is not a quota-aware mode.
