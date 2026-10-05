/** Preserve an internal task/project deep link across sign-in and session restoration. */
export function returnLocation(state: unknown): string {
  if (!state || typeof state !== 'object' || !('from' in state)) return '/';
  const from = state.from;
  if (!from || typeof from !== 'object' || !('pathname' in from)) return '/';
  const path = from.pathname;
  if (
    typeof path !== 'string' ||
    !path.startsWith('/') ||
    path.startsWith('//') ||
    path.includes('\\') ||
    [...path].some((character) => character.charCodeAt(0) <= 32) ||
    path === '/login'
  )
    return '/';
  const search =
    'search' in from && typeof from.search === 'string' && from.search.startsWith('?')
      ? from.search
      : '';
  const hash =
    'hash' in from && typeof from.hash === 'string' && from.hash.startsWith('#') ? from.hash : '';
  return path + search + hash;
}
