// These APIs return public game data, independent of the signed-in account.
const PUBLIC_GAME_ROUTES = new Set([
  'catalog',
  'categories',
  'items',
  'locations',
  'characters',
  'quests',
  'dialogue',
  'story-map',
  'maps',
  'nodes',
  'graph',
  'search',
  'releases',
]);

export function requestCredentials(path: string, method: string): RequestCredentials {
  const root = path.split(/[/?#]/).filter(Boolean)[0];
  return method.toUpperCase() === 'GET' && PUBLIC_GAME_ROUTES.has(root) ? 'omit' : 'include';
}
