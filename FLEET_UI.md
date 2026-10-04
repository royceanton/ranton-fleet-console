# Fleet Activity: actual Codex activity and fleet jobs

Fleet Activity extends the existing console using its themes, management authentication and quota pages. Its default **Codex** source shows the owner’s actual projects and named chats on this Mac. **Fleet jobs** holds the coordinator’s registered projects and submitted work. Setup jobs never stand in for the owner’s app project list.

Codex uses a project rail, searchable main-chat list and focused inspector. Recent bounds the initial list to 30; Working includes parents with active descendants; All shows supported local unarchived records. State combines saved turn status with a read-only writer probe. Missing history remains unknown. The inspector exposes the real chat link, latest six operations, older operations, agent children and their parent links. Workspace facts are disclosed separately. No fleet completion/review buttons appear on ordinary app chats.

Fleet jobs retains Tasks and Activity, automatic account selection, bounded routing controls and Result/Activity/Details. Job registration may record an originating control-chat UUID/title; related jobs inherit it within the same project. This explicit job provenance differs from Codex’s observed parent-agent relationships. Native worker session IDs are never presented as app chat IDs.

Source, project, selection, query and filters are URL state. Query changes preserve input focus, and authentication preserves safe internal return routes. Phone selection replaces the list; Back restores focus after rendering. A native labeled project select replaces the desktop rail. Activity resolves operations to their actual chat and project. Only metadata and redacted command evidence are exported: no messages, reasoning, command output, tool arguments or provider payloads.

## Design review

Applied the installed user-requested web-design-guidelines and ui-ux-pro-max skills from their canonical GitHub repositories. Upstream theme tokens/components remain. Marketing patterns and replacement palettes were discarded. No new frontend dependency or external font was added.

Fresh Web Interface Guidelines review on 4 October 2026:

- `src/features/fleet/CodexActivity.tsx:121` — fixed Back focus timing; restore after committed selection change, with work-area fallback.
- `src/features/fleet/CodexActivity.tsx` — pass: semantic links/buttons, labeled search/select, visible focus, escaped text, bounded initial chat/operation lists, empty/error states, locale date formatting.
- `src/features/fleet/FleetActivityPage.module.scss` — pass: touch targets, long-token wrapping, bounded project rail, content visibility on large history, reduced motion, responsive master/detail.
- `src/router/returnLocation.ts`, `src/components/common/PageTransition.tsx` — pass: internal return allowlist and query-aware navigation.

## Verification and limits

Frontend verify: 1,516 tests, lint, type checking and single-file build pass. Native distribution: 34 tests pass; canonical bootstrap: 31 pass. Tests cover actual metadata membership, inherited projects, stale unfinished turns, read-only storage, privacy, missing/unsupported schemas, authentication and nested/cyclic agent filtering.

Live normal-Chrome QA checks the actual project list, Attendix search and six child agents, child-to-main links, command disclosure, project-scoped activity, reload and Back navigation, source separation and phone overflow/focus. This is local read-only observation, not a claim to see every remote/cloud chat or route existing app inference. iPhone app deep-link handling requires a device check.

## Read recovery

A temporary record-read failure retains verified projects/chats with their original observation time and an explicit stale notice. Live state becomes unknown; a new prompt is not required. Safe categories distinguish updating metadata, busy databases, unsafe paths and unreadable records. Refresh recovery clears the notice. Without any verified snapshot, the view remains unavailable. Ownership/path failures discard caches. A chat history refresh can retain verified operation evidence and shows a separate retry notice. Cached history is not a live execution claim.

## Quota ledger

Quota Management defaults to a Codex ledger modelled on the owner-supplied Theo screenshot. Its summary sums like weekly windows across independent allowances, shows the next reset and reads the coordinator's decision. This is not a token pool. Account aliases require verified bindings; public email/filename is the fallback. Row details reuse the complete original provider card, and Cards mode remains available. New row actions are 44px; the phone summary uses a full-width balance and two compact reset/routing cells. Other providers retain upstream card behavior.

Visible Codex accounts refresh every 90 seconds through existing generation/in-flight guards. Last verified values, metadata and timeline survive refresh/error attempts with the original observation time. Unknown windows are not synthesized; optional subscription enrichment never renews the primary quota timestamp. Router status is separate, polled on the existing authenticated endpoint. No automatic reset consumption or new scheduling policy is introduced.

Fresh web-design-guidelines review: CodexLedger.tsx, CodexLedger.module.scss, QuotaPage.tsx and QuotaPage.module.scss pass after fixing refresh layout shifts, the shared minute-clock boundary, mobile density, readable status colors, touch targets and semantic/localized controls. No new UI library or font was introduced. Regression/build checks passed: 1,516 frontend tests, 34 distribution tests and 31 canonical tests.
