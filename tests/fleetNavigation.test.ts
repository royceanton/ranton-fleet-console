import { expect, test } from 'bun:test';
import { returnLocation } from '../src/router/returnLocation';

test('sign-in restoration retains task, project, filter and inspector deep links', () => {
  expect(
    returnLocation({
      from: {
        pathname: '/fleet',
        search: '?task=worker&project=project&filter=done&detail=activity',
        hash: '',
      },
    })
  ).toBe('/fleet?task=worker&project=project&filter=done&detail=activity');
  expect(
    returnLocation({ from: { pathname: '/quota', search: '?provider=codex', hash: '#accounts' } })
  ).toBe('/quota?provider=codex#accounts');
});
test('malformed or external return destinations cannot replace the internal route', () => {
  for (const pathname of [
    'https://example.com',
    '//example.com',
    '/\\example.com',
    '/login',
    '/\nexample.com',
  ])
    expect(returnLocation({ from: { pathname } })).toBe('/');
  for (const value of [null, [], { from: false }, { from: { pathname: 42 } }])
    expect(returnLocation(value)).toBe('/');
});
