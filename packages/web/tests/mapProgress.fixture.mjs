// Standalone browser-test API: no database, queues or game placements.
import { createServer } from 'node:http';

const maps = [
  { id: 1, game_map_id: 8, name: 'Huanglong' },
  { id: 2, game_map_id: 102, name: 'Chronorift Metropolis' },
].map(({ name, ...map }) => ({
  ...map,
  game_version: '3.7.0',
  asset_job_id: 'map-progress-fixture',
  layer: 'surface',
  grid_bounds: [-1, -1, 2, 2],
  metadata: { catalog: { names: { en: name }, locations: [] } },
  tiles: [],
}));
const points = [
  ['chest', 'Basic Supply Chest', 'Treasure001', -65000, 65000],
  ['chest', 'Advanced Supply Chest', 'Treasure015', 65000, 65000],
  ['collectible', 'Sonance Casket: Ragunna', 'CollectFixture', -65000, -65000],
  ['collectible', 'Windchimer', 'CollectWindFixture', 65000, -65000],
  ['collectible', 'Unclaimed Rafter Kite', 'branch3.5_21_Gameplay_3_0/Common4', 0, 0],
  ['teleport', 'Resonance Nexus', 'MapMark', 0, 65000],
  ['resource', 'Scarletthorn', 'Collect501', -65000, 0],
  ['collectible', 'Floramber', 'Collect504', 65000, 0],
  ['collectible', 'Septimont Sonance Casket Collector', 'MapMark', 0, -65000],
  ['boss', 'Boss fixture', 'MonsterFixture', 35000, 35000],
  ['tacet_field', 'Tacet Field fixture', 'MapMark', -35000, -35000],
].map(([category, name, blueprint_type, x, y], index) => ({
  id: index + 101,
  entity_id: index + 1001,
  category,
  blueprint_type,
  world: [x, y, 0],
  metadata: { names: { en: name }, hidden: name === 'Unclaimed Rafter Kite' },
}));
const server = createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost');
  res.setHeader('Access-Control-Allow-Origin', 'http://localhost:5174');
  res.setHeader('Access-Control-Allow-Credentials', 'true');
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('Content-Type', 'application/json');
  if (url.pathname === '/maps') return res.end(JSON.stringify(maps));
  const map = maps.find((item) => url.pathname === `/maps/${item.id}`);
  if (map) return res.end(JSON.stringify(map));
  if (/^\/maps\/[12]\/markers$/.test(url.pathname)) {
    // Same source entity in another region must have independent progress.
    const items = url.pathname.includes('/2/') ? points.slice(0, 1) : points;
    return res.end(JSON.stringify({ items, next_after_id: null }));
  }
  res.writeHead(404);
  res.end(JSON.stringify({ detail: 'Fixture route not found' }));
});
server.listen(8013, '127.0.0.1', () => console.log('Map progress fixture API: 8013'));
