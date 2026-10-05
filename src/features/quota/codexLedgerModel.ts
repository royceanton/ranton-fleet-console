import type { AuthFileItem, CodexQuotaState, CodexQuotaWindow } from '@/types';
import type { FleetStatus } from '@/features/fleet/api';
import { getQuotaCacheKey } from '@/utils/quota/identity';
import type { QuotaFileEntry } from './logic';
import type { QuotaCardState } from './providers';

export function hasCodexObservation(quota?: CodexQuotaState): quota is CodexQuotaState {
  return Boolean(
    quota &&
    (quota.status === 'success' ||
      (typeof quota.observedAtMs === 'number' &&
        Number.isFinite(quota.observedAtMs) &&
        quota.windows.length))
  );
}

/** Keep the measured values during an attempt; never make its timestamp fresh. */
export function preserveCodexObservation(
  previous: QuotaCardState | undefined,
  next: QuotaCardState
): QuotaCardState {
  if (
    !previous ||
    !('windows' in previous) ||
    !('observedAtMs' in previous) ||
    typeof previous.observedAtMs !== 'number' ||
    !Number.isFinite(previous.observedAtMs) ||
    !Array.isArray(previous.windows) ||
    !previous.windows.length ||
    (next.status !== 'loading' && next.status !== 'error')
  )
    return next;
  return { ...previous, status: next.status, error: next.error, errorStatus: next.errorStatus };
}

export const QUOTA_FRESH_MS = 300_000;
export function remainingPercent(window?: CodexQuotaWindow): number | null {
  const used = window?.usedPercent;
  return typeof used === 'number' && Number.isFinite(used)
    ? 100 - Math.min(100, Math.max(0, used))
    : null;
}

/** Labels must come from verified bindings, never filename/email guesses. */
export function accountAlias(
  file: AuthFileItem,
  routing: Pick<FleetStatus, 'accountBindings'> | null
): string {
  const index = file.authIndex == null ? '' : String(file.authIndex);
  const matches = routing?.accountBindings.filter((binding) => binding.authIndex === index) ?? [];
  return index && matches.length === 1 ? matches[0].account : '';
}
export function accountLabel(
  file: AuthFileItem,
  routing: Pick<FleetStatus, 'accountBindings'> | null
): string {
  const alias = accountAlias(file, routing);
  return alias ? alias.charAt(0).toUpperCase() + alias.slice(1) : file.email?.trim() || file.name;
}
export function quotaFresh(quota: CodexQuotaState | undefined, now: number): boolean {
  const at = quota?.observedAtMs;
  return (
    quota?.status === 'success' &&
    typeof at === 'number' &&
    Number.isFinite(at) &&
    // The UI shares a minute clock, so a just-completed observation can be newer.
    at <= now + 60_000 &&
    now - at < QUOTA_FRESH_MS
  );
}
export function ledgerWindows(accounts: QuotaFileEntry[], quotas: Record<string, CodexQuotaState>) {
  const columns = new Map<string, CodexQuotaWindow>();
  for (const { file } of accounts) {
    const quota = quotas[getQuotaCacheKey(file)];
    if (!hasCodexObservation(quota)) continue;
    for (const window of quota.windows) columns.set(window.id, window);
  }
  const rank = (id: string) => (id === 'weekly' ? 0 : id === 'five-hour' ? 1 : 2);
  return [...columns.values()].sort((a, b) => rank(a.id) - rank(b.id));
}
export function weeklyCapacity(
  accounts: QuotaFileEntry[],
  quotas: Record<string, CodexQuotaState>
) {
  let reported = 0;
  let remaining = 0;
  for (const { file } of accounts) {
    const quota = quotas[getQuotaCacheKey(file)];
    const value = hasCodexObservation(quota)
      ? remainingPercent(quota.windows.find((window) => window.id === 'weekly'))
      : null;
    if (value !== null) {
      reported += 1;
      remaining += value;
    }
  }
  return {
    reported,
    total: accounts.length,
    remaining: reported ? remaining : null,
    maximum: accounts.length * 100,
  };
}
export function nextWeeklyReset(
  accounts: QuotaFileEntry[],
  quotas: Record<string, CodexQuotaState>,
  now: number
) {
  return (
    accounts
      .flatMap(({ file }) => {
        const quota = quotas[getQuotaCacheKey(file)];
        const reset = hasCodexObservation(quota)
          ? quota.windows.find((window) => window.id === 'weekly')?.resetAtMs
          : null;
        return typeof reset === 'number' && Number.isFinite(reset) && reset > now
          ? [{ file, reset }]
          : [];
      })
      .sort((a, b) => a.reset - b.reset)[0] ?? null
  );
}
