import { describe, expect, test } from 'bun:test';
import { decodeStatus, decodeTask, orderTasks, fleetApi } from '../src/features/fleet/api';
import en from '../src/i18n/locales/en.json';
import ru from '../src/i18n/locales/ru.json';
import zhCN from '../src/i18n/locales/zh-CN.json';
import zhTW from '../src/i18n/locales/zh-TW.json';
import {
  activityItems,
  controlChatLink,
  matchesTask,
  projectGroups,
  resultPreview,
} from '../src/features/fleet/presentation';
import { ResultText } from '../src/features/fleet/TaskInspector';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

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
  test('project grouping and search retain orphaned tasks without inventing lineage', () => {
    const status = decodeStatus({
      name: 'ranton fleet',
      projects: [{ id: 'a', name: 'Project A' }],
      tasks: [
        {
          id: 'known',
          project: 'a',
          state: 'running',
          origin_chat_id: '11111111-2222-4333-8444-555555555555',
          origin_chat_title: 'Main control chat',
          parent_task: 'parent',
        },
        { id: 'orphan', project: 'missing', state: 'completed' },
      ],
    });
    const groups = projectGroups(status.projects, status.tasks);
    expect(groups.map((group) => group.tasks.map((task) => task.id))).toEqual([
      ['known'],
      ['orphan'],
    ]);
    expect(groups[1].project).toBeNull();
    expect(status.tasks[1].originChatTitle).toBe('');
    expect(status.tasks[0].parentTask).toBe('parent');
    expect(matchesTask(status.tasks[0], 'a', 'running', 'main control')).toBe(true);
    expect(matchesTask(status.tasks[0], 'different', 'all', '')).toBe(false);
    expect(matchesTask(status.tasks[1], '', 'open', '')).toBe(false);
    expect(matchesTask(status.tasks[1], '', 'done', '')).toBe(true);
  });
  test('only validated control chat UUIDs become Codex links', () => {
    expect(controlChatLink('11111111-2222-4333-8444-555555555555')).toBe(
      'codex://threads/11111111-2222-4333-8444-555555555555'
    );
    for (const value of ['', 'javascript:alert(1)', 'x?view=review', 'https://example.com'])
      expect(controlChatLink(value)).toBeNull();
  });
  test('project activity excludes unassigned gateway traffic and resolves the real task', () => {
    const status = decodeStatus({
      name: 'ranton fleet',
      tasks: [{ id: 'a', project: 'one' }],
      operations: [
        { task: 'gateway', item: 'g', started: 5 },
        { task: 'a', item: 'a', started: 2 },
      ],
      events: [
        { id: 1, task: 'a', at: 3 },
        { id: 2, kind: 'coordinator', at: 4 },
        { id: 3, kind: 'progress', task: 'a', at: 6, message: 'Checking the project' },
      ],
    });
    expect(
      activityItems(status.operations, status.events, status.tasks, 'one').map(
        (entry) => entry.task?.id
      )
    ).toEqual(['a', 'a']);
    expect(
      activityItems(status.operations, status.events, status.tasks, '').map((entry) => entry.at)
    ).toEqual([5, 4, 3, 2]);
  });
  test('results format text without executing HTML or unsafe Markdown links', () => {
    const markup = renderToStaticMarkup(
      createElement(ResultText, {
        value:
          '**Finished** `git status`\n\n- <script>alert(1)</script>\n- [unsafe](javascript:alert(1))\n\n[Source](https://github.com/example/project)',
      })
    );
    expect(markup).toContain('<strong>Finished</strong>');
    expect(markup).toContain('<code>git status</code>');
    expect(markup).toContain('&lt;script&gt;');
    expect(markup).not.toContain('href="javascript:');
    expect(markup).toContain('href="https://github.com/example/project"');
  });
});

test('result previews skip empty headings and retain useful findings', () => {
  expect(
    resultPreview('Review result:\n\n- Passed project checks.\n\nNext: inspect the diff.')
  ).toBe('- Passed project checks.\n\nNext: inspect the diff.');
  expect(resultPreview('a'.repeat(1000)).length).toBeLessThanOrEqual(700);
});
