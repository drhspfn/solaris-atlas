import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createServer } from 'node:http';
import { after, before, test } from 'node:test';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';

// Run after `npm run build`. No browser scripts or external API are involved.
const webDirectory = fileURLToPath(new URL('../', import.meta.url));
const requests = [];
const biography = 'Sanhua guards the magistrate of Jinzhou.';
const portrait = 'https://cdn.solarisatlas.fun/objects/test-sanhua';
const profile = {
  character: { canonical_key: 'character:1102', name: { content: 'Sanhua' } },
  source: { version: '1.0' },
};
const archive = {
  biography: `<color=white>${biography}</color>`,
  profile: { birthday: 'January 20', affiliation: 'Jinzhou', voice_actors: {} },
  artwork: [{ kind: 'RoleHeadIconLarge', engine_path: 'test', url: portrait }],
  skills: [
    {
      id: 1,
      type: 1,
      order: 1,
      name: 'Eternal Frost',
      description: 'Glacio damage',
      max_level: 10,
      icon: {},
    },
  ],
  skill_tree: [],
  resonance_chain: [],
  voice_lines: [],
  other_voice_events: [],
};
const backend = createServer(async (request, response) => {
  requests.push(request.url);
  // A cold response must still produce visible HTML, not a streamed loader.
  await delay(150);
  response.setHeader('Content-Type', 'application/json');
  if (request.url === '/characters/character%3A1102/profile?locale=en') {
    response.end(JSON.stringify(profile));
  } else if (request.url === '/characters/character%3A1102/archive?locale=en') {
    response.end(JSON.stringify(archive));
  } else if (request.url === '/items/item%3A1/profile?locale=en') {
    response.end(
      JSON.stringify({
        item: {
          canonical_key: 'item:1',
          name: { content: 'Astrite' },
          description: { content: 'A bright crystal.' },
        },
      }),
    );
  } else if (request.url === '/events?server=europe') {
    response.end(
      JSON.stringify({
        source_url: 'https://example.test/events',
        events: [
          {
            id: 1,
            source_id: 'test-event',
            title: "Artisan's Search",
            kind: 'limited',
            game_version: '3.7',
            starts_at: new Date().toISOString(),
            ends_at: new Date(Date.now() + 86400000).toISOString(),
            banner_url: 'https://cdn.solarisatlas.fun/objects/test-event',
          },
          {
            id: 2,
            source_id: 'gacha:100088',
            title: 'Lynae · Resonator Convene',
            kind: 'banner',
            catalog_version: '3.7.0',
            starts_at: null,
            ends_at: null,
            banner_url: 'https://cdn.solarisatlas.fun/objects/empty-background',
            preview_url: 'https://cdn.solarisatlas.fun/objects/featured-portrait',
          },
        ],
      }),
    );
  } else {
    response.writeHead(404).end('{}');
  }
});
let next;
let site;
let output = '';

before(
  async () => {
    backend.listen(0, '127.0.0.1');
    await once(backend, 'listening');
    const portProbe = createServer().listen(0, '127.0.0.1');
    await once(portProbe, 'listening');
    const port = portProbe.address().port;
    await new Promise((resolve) => portProbe.close(resolve));
    site = `http://127.0.0.1:${port}`;
    next = spawn(
      process.execPath,
      ['node_modules/next/dist/bin/next', 'start', '-p', String(port), '-H', '127.0.0.1'],
      {
        cwd: webDirectory,
        env: {
          ...process.env,
          INTERNAL_API_BASE: `http://127.0.0.1:${backend.address().port}`,
          NEXT_TELEMETRY_DISABLED: '1',
        },
        stdio: ['ignore', 'pipe', 'pipe'],
      },
    );
    next.stdout.on('data', (chunk) => {
      output += chunk;
    });
    next.stderr.on('data', (chunk) => {
      output += chunk;
    });
    for (let attempt = 0; attempt < 100; attempt++) {
      if (next.exitCode !== null) throw new Error(output);
      try {
        await fetch(`${site}/favicon.svg`);
        return;
      } catch {
        await delay(100);
      }
    }
    throw new Error(`Next.js did not start: ${output}`);
  },
  { timeout: 20000 },
);

after(async () => {
  if (next && next.exitCode === null) {
    const exited = once(next, 'exit');
    next.kill();
    await exited;
  }
  await new Promise((resolve) => backend.close(resolve));
});

for (const userAgent of ['Mozilla/5.0', 'TelegramBot (like TwitterBot)']) {
  test(`character details and preview are in initial HTML for ${userAgent}`, async () => {
    const response = await fetch(`${site}/characters/character%3A1102`, {
      headers: { 'User-Agent': userAgent },
    });
    assert.equal(response.status, 200);
    const html = (await response.text()).replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '');
    const head = html.match(/<head>([\s\S]*?)<\/head>/)?.[1] ?? '';
    const main = html.match(/<main>([\s\S]*?)<\/main>/)?.[1] ?? '';
    assert.match(head, /<title>Sanhua/);
    assert.ok(head.includes(`content="${biography}"`));
    assert.ok(head.includes(`content="${portrait}"`));
    assert.ok(head.includes('href="https://solarisatlas.fun/characters/character%3A1102"'));
    assert.match(main, /<h1>Sanhua/);
    for (const detail of [biography, 'January 20', 'Eternal Frost', portrait])
      assert.ok(main.includes(detail), detail);
    assert.doesNotMatch(html, /Opening archive record|Loading character details|id="S:\d+"/);
    assert.ok(requests.includes('/characters/character%3A1102/archive?locale=en'));
    assert.ok(!requests.some((path) => path.includes('%253A')));
  });
}

test('item details are visible without JavaScript', async () => {
  const response = await fetch(`${site}/items/item%3A1`);
  assert.equal(response.status, 200);
  const html = (await response.text()).replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '');
  const main = html.match(/<main>([\s\S]*?)<\/main>/)?.[1] ?? '';
  assert.match(main, /<h1>Astrite/);
  assert.ok(main.includes('A bright crystal.'));
  assert.doesNotMatch(html, /Opening archive record|id="S:\d+"/);
});

test('events fetch at request time and render their artwork without JavaScript', async () => {
  const response = await fetch(`${site}/events`);
  assert.equal(response.status, 200);
  const html = (await response.text()).replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '');
  assert.ok(html.includes('Artisan&#x27;s Search'));
  assert.ok(html.includes('src="https://cdn.solarisatlas.fun/objects/test-event"'));
  assert.ok(html.includes('src="https://cdn.solarisatlas.fun/objects/featured-portrait"'));
  assert.ok(!html.includes('src="https://cdn.solarisatlas.fun/objects/empty-background"'));
  assert.ok(html.includes('Server schedule unavailable'));
  assert.doesNotMatch(html, /Opening archive record|id="S:\d+"/);
  assert.ok(requests.includes('/events?server=europe'));
});

test('public shell groups navigation and keeps service links in the footer', async () => {
  const response = await fetch(`${site}/`);
  assert.equal(response.status, 200);
  const html = (await response.text()).replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '');
  const header = html.match(/<header\b[^>]*>([\s\S]*?)<\/header>/)?.[1] ?? '';
  const footer = html.match(/<footer\b[^>]*>([\s\S]*?)<\/footer>/)?.[1] ?? '';
  assert.match(header, /<summary[^>]*>Maps/);
  assert.match(header, /<summary[^>]*>Archive/);
  assert.doesNotMatch(header, /href="(?:\/docs|\/mcp-docs|https:\/\/github.com)/);
  for (const href of ['/docs', '/mcp-docs', '/legal']) assert.ok(footer.includes(`href="${href}"`));
  assert.doesNotMatch(html, /class="card-watermark"/);
});

test('legal information is readable in initial HTML without client scripts', async () => {
  const response = await fetch(`${site}/legal`);
  assert.equal(response.status, 200);
  const html = (await response.text()).replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '');
  assert.match(html, /<title>Legal &amp; privacy/);
  for (const id of ['about', 'terms', 'copyright', 'privacy', 'storage', 'ai', 'contact'])
    assert.ok(html.includes(`id="${id}"`));
  for (const text of ['Yevhenii Verbytskyi', 'Contabo', 'admin@solarisatlas.fun'])
    assert.ok(html.includes(text));
  assert.doesNotMatch(html, /solarisatlas\.fun@gmail\.com/);
  assert.ok(html.includes('href="https://solarisatlas.fun/legal"'));
});
