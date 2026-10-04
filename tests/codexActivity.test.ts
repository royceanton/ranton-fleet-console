import { expect, test } from 'bun:test';
import { decodeCodexStatus } from '../src/features/fleet/api';
import { descendantChats, visibleChats } from '../src/features/fleet/codexPresentation';

test('actual Codex membership and activity stay distinct from fleet queue jobs', () => {
  const status = decodeCodexStatus({
    available: true,
    projects: [{ id: 'real', name: 'Real project' }],
    chats: [
      { id: 'main', title: 'Main chat', project: 'real', state: 'idle', updated: 2 },
      { id: 'child', title: 'Worker', parent: 'main', project: 'real', state: 'idle', updated: 3 },
      {
        id: 'grandchild',
        title: 'Worker child',
        parent: 'child',
        project: 'real',
        state: 'working',
        updated: 4,
      },
      { id: 'other', title: 'Unassigned chat', state: 'unknown', updated: 1 },
    ],
  });
  expect(visibleChats(status, 'real', 'working', '').map((c) => c.id)).toEqual(['main']);
  expect(visibleChats(status, 'real', 'all', 'Real project').map((c) => c.id)).toEqual(['main']);
  expect(visibleChats(status, '', 'all', '').map((c) => c.id)).toEqual(['main', 'other']);
  expect(descendantChats(status.chats, 'main').map((c) => c.id)).toEqual(['child', 'grandchild']);
  expect(status.chats[3].state).toBe('unknown');
  expect(() => decodeCodexStatus({ name: 'ranton fleet', tasks: [] })).toThrow();
});
test('recent chats are bounded, ordered and unknown data is never assumed live', () => {
  const status = decodeCodexStatus({
    available: false,
    projects: [],
    chats: Array.from({ length: 40 }, (_, n) => ({ id: String(n), updated: n })),
  });
  expect(visibleChats(status, '', 'recent', '')).toHaveLength(30);
  expect(visibleChats(status, '', 'recent', '')[0].id).toBe('39');
  expect(status.available).toBe(false);
  expect(status.chats[0].state).toBe('');
  expect(visibleChats(status, '', 'working', '')).toEqual([]);
});
test('cyclic agent ancestry is bounded without inventing a main chat', () => {
  const status = decodeCodexStatus({
    available: true,
    projects: [],
    chats: [
      { id: 'a', parent: 'b' },
      { id: 'b', parent: 'a' },
    ],
  });
  expect(descendantChats(status.chats, 'a').map((c) => c.id)).toEqual(['b']);
  expect(visibleChats(status, '', 'all', '')).toEqual([]);
});

test('literal Fleet UI labels exist instead of exposing translation keys', async () => {
  const locale = (await import('../src/i18n/locales/en.json')).default;
  for (const file of ['CodexActivity', 'FleetActivityPage', 'TaskInspector', 'FleetControls']) {
    const source = await Bun.file(
      new URL('../src/features/fleet/' + file + '.tsx', import.meta.url)
    ).text();
    for (const [, key] of source.matchAll(/\bt\('([^']+)'(?:,|\))/g)) {
      let value: unknown = locale;
      for (const part of key.split('.'))
        value = value && typeof value === 'object' ? Reflect.get(value, part) : null;
      expect(value, key).toBeTruthy();
    }
  }
});
