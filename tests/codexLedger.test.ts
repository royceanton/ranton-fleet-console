import { expect, test } from 'bun:test';
import type { CodexQuotaState, CodexQuotaWindow } from '@/types';
import { decodeStatus } from '@/features/fleet/api';
import {
  accountAlias,
  accountLabel,
  ledgerWindows,
  nextWeeklyReset,
  quotaFresh,
  remainingPercent,
  preserveCodexObservation,
  weeklyCapacity,
} from '@/features/quota/codexLedgerModel';
import { filterEntriesBySearch, type QuotaFileEntry } from '@/features/quota/logic';
import { CODEX_CONFIG } from '@/features/quota/providers/codex/data';
import { buildTimelineLane } from '@/features/quota/quotaTimelineModel';

const now = 1_800_000_000_000;
const entry = (name: string, index = name): QuotaFileEntry => ({
  type: 'codex',
  file: { name, authIndex: index, email: name + '@example.test' },
});
const accounts = [entry('one'), entry('two')];
const window = (used: number | null, id = 'weekly', reset = now + 60_000): CodexQuotaWindow => ({
  id,
  label: id,
  usedPercent: used,
  resetLabel: '',
  resetAtMs: reset,
});
const state = (used: number | null): CodexQuotaState => ({
  status: 'success',
  observedAtMs: now,
  windows: [window(used)],
});
const quotas = { one: state(29), two: state(8) };

test('weekly capacity sums only like windows across independent accounts', () => {
  expect(weeklyCapacity(accounts, quotas)).toEqual({
    reported: 2,
    total: 2,
    remaining: 163,
    maximum: 200,
  });
  expect(ledgerWindows(accounts, quotas).map((window) => window.id)).toEqual(['weekly']);
});
test('partial and failed observations stay visibly partial rather than a fabricated balance', () => {
  const missing = weeklyCapacity(accounts, { one: quotas.one });
  expect(missing).toEqual({ reported: 1, total: 2, remaining: 71, maximum: 200 });
  expect(weeklyCapacity(accounts, {}).remaining).toBeNull();
  expect(
    weeklyCapacity(accounts, { one: { status: 'error', windows: [window(0)] } }).reported
  ).toBe(0);
  expect(weeklyCapacity(accounts, { one: state(null) }).remaining).toBeNull();
  expect(weeklyCapacity(accounts, { one: state(100) }).remaining).toBe(0);
});

test('refreshing and failed requests retain verified numbers and metadata without advancing freshness', () => {
  const old = { ...quotas.one, creditBalance: '0', rateLimitResetCreditsAvailableCount: 3 };
  old.windows = [{ ...old.windows[0], periodHours: 168 }];
  const pending = preserveCodexObservation(old, { status: 'loading', windows: [] });
  expect(pending.status).toBe('loading');
  expect(
    buildTimelineLane({ name: 'one', displayName: 'One', provider: 'codex', quota: pending })
      .remaining
  ).toBe(71);
  expect(pending).toMatchObject({
    observedAtMs: now,
    creditBalance: '0',
    rateLimitResetCreditsAvailableCount: 3,
  });
  const failed = preserveCodexObservation(pending, {
    status: 'error',
    windows: [],
    error: 'Unavailable',
  });
  expect(failed.status).toBe('error');
  expect(weeklyCapacity(accounts, { one: failed as CodexQuotaState }).remaining).toBe(71);
  expect(quotaFresh(failed as CodexQuotaState, now)).toBe(false);
  expect(old.status).toBe('success');
  expect(preserveCodexObservation(undefined, { status: 'error', windows: [] })).toEqual({
    status: 'error',
    windows: [],
  });
  const recovered = state(40);
  expect(preserveCodexObservation(failed, recovered)).toBe(recovered);
});
test('secondary and model-specific windows are preserved without mixing them into weekly balance', () => {
  const extra = {
    one: {
      ...state(29),
      windows: [window(60, 'five-hour'), window(29), window(20, 'code-review-weekly')],
    },
  };
  expect(ledgerWindows(accounts, extra).map((window) => window.id)).toEqual([
    'weekly',
    'five-hour',
    'code-review-weekly',
  ]);
  expect(weeklyCapacity(accounts, extra).remaining).toBe(71);
});
test('unknown, nonfinite and malformed used percentages never become full capacity', () => {
  expect(remainingPercent()).toBeNull();
  expect(remainingPercent(window(null))).toBeNull();
  expect(remainingPercent(window(NaN))).toBeNull();
  expect(remainingPercent(window(Infinity))).toBeNull();
  expect(remainingPercent(window(150))).toBe(0);
  expect(remainingPercent(window(-20))).toBe(100);
});
test('aliases require exactly one verified public credential binding', () => {
  const routing = { accountBindings: [{ account: 'harith', authIndex: 'one' }] };
  expect(accountLabel(accounts[0].file, routing)).toBe('Harith');
  expect(accountAlias(accounts[1].file, routing)).toBe('');
  expect(accountLabel(accounts[1].file, routing)).toBe('two@example.test');
  expect(
    accountAlias(accounts[0].file, {
      accountBindings: [...routing.accountBindings, { account: 'jill', authIndex: 'one' }],
    })
  ).toBe('');
  expect(accountAlias({ name: 'harith-account.json' }, routing)).toBe('');
});
test('quota freshness is based on the observation, never the scheduled reset or router choice', () => {
  expect(quotaFresh(quotas.one, now)).toBe(true);
  expect(quotaFresh(quotas.one, now + 300_000)).toBe(false);
  expect(quotaFresh(quotas.one, now - 1)).toBe(true);
  expect(quotaFresh(quotas.one, now - 60_001)).toBe(false);
  expect(quotaFresh({ ...quotas.one, observedAtMs: undefined }, now)).toBe(false);
  expect(remainingPercent(quotas.one.windows[0])).toBe(71);
});
test('next reset is the earliest future weekly observation, excluding elapsed and other windows', () => {
  const data = {
    one: {
      ...state(29),
      windows: [window(29, 'weekly', now - 1), window(10, 'five-hour', now + 1)],
    },
    two: quotas.two,
  };
  expect(nextWeeklyReset(accounts, data, now)?.file.name).toBe('two');
  expect(nextWeeklyReset(accounts, {}, now)).toBeNull();
});
test('search supports verified aliases without searching credential-bearing account fields', () => {
  const files = [{ ...accounts[0], file: { ...accounts[0].file, account: 'PRIVATE-KEY' } }];
  expect(filterEntriesBySearch(files, 'Harith', () => 'Harith')).toHaveLength(1);
  expect(filterEntriesBySearch(files, 'PRIVATE-KEY', () => 'Harith')).toHaveLength(0);
});

test('API decoding only accepts configured account aliases and preserves unknown mappings', () => {
  const status = decodeStatus({
    name: 'ranton fleet',
    tasks: [],
    routing: {
      accountBindings: [
        { account: 'harith', authIndex: 'one' },
        { account: 'stranger', authIndex: 'two' },
        { account: 'jill' },
      ],
    },
  });
  expect(status.accountBindings).toEqual([{ account: 'harith', authIndex: 'one' }]);
  expect(decodeStatus({ name: 'ranton fleet', tasks: [] }).accountBindings).toEqual([]);
});
test('success construction preserves the original primary observation time', () => {
  const data = {
    observedAtMs: now,
    planType: null,
    subscriptionActiveUntil: null,
    creditBalance: null,
    creditsUnlimited: false,
    rateLimitResetCreditsAvailableCount: null,
    rateLimitResetCreditsApplicableAvailableCount: null,
    rateLimitResetCredits: [],
    rateLimitResetCreditsError: '',
    windows: [window(29)],
  };
  expect(CODEX_CONFIG.buildSuccessState(data).observedAtMs).toBe(now);
  expect(
    CODEX_CONFIG.buildSuccessState({ ...data, subscriptionActiveUntil: now + 100 }).observedAtMs
  ).toBe(now);
});
