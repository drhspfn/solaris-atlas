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
const notice = {
  id: 42,
  source_id: 'official-42',
  title: 'Official Artisan event rules',
  category: 'game',
  banner_url: 'https://cdn.solarisatlas.fun/objects/notice-banner',
  available_from: '2026-10-08T00:00:00Z',
  observed_at: '2026-10-19T00:00:00Z',
  source_url: 'https://aki-gm-resources-oversea.aki-game.net/aki/announcement/index.html',
  revision: 'a'.repeat(64),
  related_event_ids: [1],
  revisions: ['a'.repeat(64)],
  blocks: [
    { type: 'text', text: 'Complete the event to obtain Astrite.' },
    { type: 'image', url: 'https://cdn.solarisatlas.fun/objects/notice-banner' },
  ],
  links: ['https://www.youtube.com/watch?v=official'],
  events: [{ id: 1, title: "Artisan's Search" }],
};
const profile = {
  character: { canonical_key: 'character:1102', name: { content: 'Sanhua' } },
  source: { version: '1.0' },
};
const testQuest = {
  game_quest_id: 139000037, canonical_key: 'quest:139000037', name: { content: '' },
  classification: {
    kind: 'test', label: 'Test quest',
    explanation: "The game's quest type assigns this record to its test category.",
    internal_name: '测试21020015武器',
    evidence: [{ field: 'QuestType.IsShowInQuestPanel', value: false }],
    source: { file: 'BinData/QuestData/questdata.json', row: 253, release_id: 5 },
  },
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
  voice_lines: [{ id: 1, order: 1, title: 'English voice title', text: 'English text stays independent of the audio track.',
    audio_url: 'https://cdn.solarisatlas.fun/objects/english-voice',
    audio_tracks: { en: 'https://cdn.solarisatlas.fun/objects/english-voice', ja: 'https://cdn.solarisatlas.fun/objects/japanese-voice' },
    audio_event_path: '/Game/Voice/Test' }],
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
  } else if (request.url === '/items/595988-emerald-of-genesis/profile?locale=en') {
    response.end(JSON.stringify({
      item: { canonical_key: 'item:21020015', slug: '595988-emerald-of-genesis',
        name: { content: 'Emerald of Genesis' } },
      banner_sources: [{ view_id: 32, banner_type: 'permanent_weapon', roles: ['preview', 'display'],
        source: { file: 'BinData/gacha/gachaviewinfo.json', row: 2, game_version: '2.0.0' } }],
      quest_references: [{ quest: testQuest, references: [{ interpretation: {
        label: 'Requires obtaining this item', is_reward: false } }] }],
    }));
  } else if (request.url === '/quests/139000037/profile?locale=en') {
    response.end(JSON.stringify({ quest: testQuest, item_requirements: [{ item_id: 21020015,
      label: 'Requires obtaining this item', count: 1,
      item: { href: '/items/595988-emerald-of-genesis', label: 'Emerald of Genesis' } }] }));
  } else if (request.url.startsWith('/quests/139000037/transcript?')) {
    response.end(JSON.stringify({ quest: testQuest, lines: [], scenes: [] }));
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
  } else if (
    ['/items/item%3A3/profile?locale=en', '/items/103-astrite/profile?locale=en'].includes(
      request.url,
    )
  ) {
    response.end(
      JSON.stringify({
        item: {
          canonical_key: 'item:3',
          slug: '103-astrite',
          href: '/items/103-astrite',
          name: { content: 'Astrite' },
          description: { content: 'A bright crystal with a stable public address.' },
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
            slug: '1-artisans-search',
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
          {
            id: 3,
            source_id: 'permanent-mode',
            title: 'Permanent test mode',
            kind: 'permanent',
            starts_at: '2026-01-01T00:00:00Z',
            ends_at: null,
          },
        ],
      }),
    );
  } else if (request.url.startsWith('/notices?')) {
    response.end(JSON.stringify({ notices: [notice], has_more: false }));
  } else if (request.url === '/notices/42') {
    response.end(JSON.stringify(notice));
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

test('weapon sources and test quest semantics are visible in server HTML', async () => {
  const item = await fetch(`${site}/items/595988-emerald-of-genesis`);
  assert.equal(item.status, 200);
  const html = (await item.text()).replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '');
  for (const value of ['Permanent weapon Convene', 'Listed in banner preview', 'Test quest',
    'Requires obtaining this item', 'not a reward']) assert.ok(html.includes(value), value);
  assert.ok(!html.includes('No confirmed acquisition sources recorded yet.'));
  const quest = await fetch(`${site}/quests/139000037`);
  assert.equal(quest.status, 200);
  const questHtml = (await quest.text()).replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '');
  for (const value of ['Test quest', 'Classification evidence', '测试21020015武器',
    'Requires obtaining this item', 'Emerald of Genesis']) assert.ok(questHtml.includes(value), value);
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

test('announcements list renders official titles and images in initial HTML', async () => {
  const response = await fetch(`${site}/notices`);
  assert.equal(response.status, 200);
  const html = (await response.text()).replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '');
  for (const value of [
    'Official Artisan event rules',
    'notice-banner',
    'Highlights',
    'Info',
    'News',
  ])
    assert.ok(html.includes(value), value);
  assert.ok(html.includes('href="/notices/42"'));
});

test('announcement details and social preview are server rendered', async () => {
  const response = await fetch(`${site}/notices/42`);
  assert.equal(response.status, 200);
  const html = (await response.text()).replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '');
  assert.match(html, /<title>Official Artisan event rules/);
  assert.ok(html.includes('Complete the event to obtain Astrite.'));
  assert.ok(html.includes('src="https://cdn.solarisatlas.fun/objects/notice-banner"'));
  assert.ok(html.includes('href="/events?event=1"'));
});

test('an event link restores event selection on the server', async () => {
  const response = await fetch(`${site}/events?event=2`);
  assert.equal(response.status, 200);
  const html = (await response.text()).replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '');
  assert.ok(html.includes('aria-label="Selected events"'));
  assert.doesNotMatch(html, /class="events-bar"/);
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

test('legacy item URL redirects to its stored slug without losing selection parameters', async () => {
  const response = await fetch(
    `${site}/items/item%3A3?locale=uk&game_version=1.3.0&line=dialogue%3A7`,
    { redirect: 'manual' },
  );
  assert.equal(response.status, 308);
  const destination = new URL(response.headers.get('location'), site);
  assert.equal(destination.pathname, '/items/103-astrite');
  assert.equal(destination.searchParams.get('locale'), 'uk');
  assert.equal(destination.searchParams.get('game_version'), '1.3.0');
  assert.equal(destination.searchParams.get('line'), 'dialogue:7');
});

test('slug item page returns details and canonical social URL in initial HTML', async () => {
  const response = await fetch(`${site}/items/103-astrite`, { redirect: 'manual' });
  assert.equal(response.status, 200);
  const html = (await response.text()).replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '');
  assert.ok(html.includes('A bright crystal with a stable public address.'));
  assert.ok(html.includes('/items/103-astrite'));
  assert.doesNotMatch(html, /Opening archive record/);
  assert.ok(requests.includes('/items/103-astrite/profile?locale=en'));
});

test('event slug links restore the existing event identity', async () => {
  const response = await fetch(`${site}/events?event=1-artisans-search`);
  const html = (await response.text()).replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '');
  assert.ok(html.includes('Artisan&#x27;s Search'));
  assert.ok(html.includes('Remove Artisan'));
  assert.ok(!html.includes('Dates not recorded'));
});

test('calendar and announcements share their archive header and navigation', async () => {
  for (const path of ['/events', '/notices']) {
    const response = await fetch(`${site}${path}`);
    const html = (await response.text()).replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '');
    assert.ok(html.includes('class="event-archive-heading"'));
    assert.ok(html.includes('class="event-archive-nav"'));
    assert.ok(html.includes('Event calendars, official announcements and every recorded run.'));
    assert.match(html, /aria-label="Events and announcements"/);
  }
});

test('voice lines render text and a custom multilingual player without native audio controls', async () => {
  const response = await fetch(`${site}/characters/character%3A1102`);
  const html = (await response.text()).replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '');
  assert.ok(html.includes('English text stays independent of the audio track.'));
  assert.ok(html.includes('Voice line text language'));
  assert.ok(html.includes('Audio track: English voice title'));
  assert.ok(html.includes('Play English voice title'));
  assert.ok(html.includes('Seek: English voice title'));
  assert.ok(html.includes('Volume: English voice title'));
  assert.ok(html.includes('Player settings: English voice title'));
  assert.ok(html.includes('english-voice'));
  assert.ok(html.includes('日本語'));
  assert.doesNotMatch(html, /<audio\b[^>]*\bcontrols(?:=|\s|>)/);
});

test('permanent open-ended content is listed below the calendar without an endless bar', async () => {
  const response = await fetch(`${site}/events`);
  const html = (await response.text()).replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '');
  assert.ok(html.includes('<h2>Permanent content</h2>'));
  assert.ok(html.includes('<strong>Permanent test mode</strong>'));
  assert.ok(html.includes('class="events-calendar-group"'));
  assert.doesNotMatch(html, /aria-label="Permanent test mode,/);
});
