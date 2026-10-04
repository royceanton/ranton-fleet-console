import { describe, expect, test } from 'bun:test';
import { decodeStatus, decodeTask, orderTasks, fleetApi } from '../src/features/fleet/api';
import en from '../src/i18n/locales/en.json';
import ru from '../src/i18n/locales/ru.json';
import zhCN from '../src/i18n/locales/zh-CN.json';
import zhTW from '../src/i18n/locales/zh-TW.json';

describe('fleet observation contract', () => {
  test('unknown quota and gateway counters remain unknown; both windows survive', () => {
    const status = decodeStatus({
      name: 'ranton fleet',
      tasks: [],
      accounts: [
        {
          alias: 'jill',
          quotaWindows: [
            {
              primary: { remainingPercent: null },
              secondary: { remainingPercent: 0, resetsAt: 123 },
            },
          ],
        },
      ],
    });
    expect(status.gatewayRequests).toBeNull();
    expect(status.accounts[0].windows.map((window) => window.remainingPercent)).toEqual([null, 0]);
    expect(status.accounts[0].eligible).toBe(false);
    expect(() => decodeStatus({ tasks: [], name: 'different-service' })).toThrow();
  });
  test('human attention sorts ahead of active work without mutating the snapshot', () => {
    const tasks = [
      decodeTask({ id: 'running', state: 'running', updated: 99 }),
      decodeTask({ id: 'review', state: 'review', updated: 1 }),
      decodeTask({ id: 'input', state: 'needs_input', updated: 2 }),
    ];
    expect(orderTasks(tasks).map((task) => task.id)).toEqual(['input', 'review', 'running']);
    expect(tasks[0].id).toBe('running');
  });
  test('malformed optional data does not become authority to enable dispatch or edits', () => {
    const status = decodeStatus({
      name: 'ranton fleet',
      tasks: [null],
      dispatchEnabled: 'true',
      accounts: null,
      projects: [{ writable: 'true' }],
      operations: false,
      gateway: { requests: '100' },
    });
    expect(status.dispatchEnabled).toBe(false);
    expect(status.projects[0].writable).toBe(false);
    expect(status.gatewayRequests).toBeNull();
    expect(status.tasks[0].id).toBe('');
  });
  test('unrecognized action cannot enter an API path', () => {
    expect(() => Reflect.apply(fleetApi.action, null, ['task', 'delete/../../config'])).toThrow(
      'Unknown fleet action'
    );
  });
  test('every supported language has the same feature keys and state labels', () => {
    for (const locale of [ru, zhCN, zhTW]) {
      expect(Object.keys(locale.fleet).sort()).toEqual(Object.keys(en.fleet).sort());
      expect(Object.keys(locale.fleet.states).sort()).toEqual(Object.keys(en.fleet.states).sort());
      expect(locale.nav.fleet_activity).toBeTruthy();
    }
  });
});
