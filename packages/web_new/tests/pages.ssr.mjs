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
const requestTimes = [];
const biography = 'Sanhua guards the magistrate of Jinzhou.';
const portrait = 'https://cdn.solarisatlas.fun/objects/test-sanhua';
const sharedQuestArtwork = 'https://cdn.solarisatlas.fun/objects/shared-quest-art';
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
    {
      type: 'image',
      url: 'https://cdn.solarisatlas.fun/objects/notice-banner',
    },
  ],
  links: ['https://www.youtube.com/watch?v=official'],
  events: [{ id: 1, title: "Artisan's Search" }],
};
const profile = {
  character: { canonical_key: 'character:1102', name: { content: 'Sanhua' } },
  source: { version: '1.0' },
};
const testQuest = {
  game_quest_id: 139000037,
  canonical_key: 'quest:139000037',
  name: { content: '' },
  classification: {
    kind: 'test',
    label: 'Test quest',
    explanation: "The game's quest type assigns this record to its test category.",
    internal_name: '测试21020015武器',
    evidence: [{ field: 'QuestType.IsShowInQuestPanel', value: false }],
    source: {
      file: 'BinData/QuestData/questdata.json',
      row: 253,
      release_id: 5,
    },
  },
};
const archive = {
  biography: `<color=white>${biography}</color>`,
  profile: { birthday: 'January 20', affiliation: 'Jinzhou', voice_actors: {} },
  artwork: [
    {
      kind: 'RoleHeadIconLarge',
      engine_path: 'test',
      url: portrait,
      scope: 'character',
    },
    {
      kind: 'RoleStand',
      engine_path: '/Game/UiQuest/ChuanShuo1',
      url: sharedQuestArtwork,
      scope: 'shared_resource',
    },
    {
      kind: 'Icon',
      engine_path: '/Game/IconRup/1503',
      url: null,
      scope: 'shared_resource',
    },
    {
      kind: 'RolePortrait',
      engine_path: '/Game/PixActivity/T_ActivityRoleYangyang',
      url: 'https://cdn.solarisatlas.fun/objects/foreign-yangyang',
      scope: 'shared_resource',
    },
  ],
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
  voice_lines: [
    {
      id: 1,
      order: 1,
      title: 'English voice title',
      text: 'English text stays independent of the audio track.',
      audio_url: 'https://cdn.solarisatlas.fun/objects/english-voice',
      audio_tracks: {
        en: 'https://cdn.solarisatlas.fun/objects/english-voice',
        ja: 'https://cdn.solarisatlas.fun/objects/japanese-voice',
      },
      audio_event_path: '/Game/Voice/Test',
    },
  ],
  other_voice_events: [],
};
const backend = createServer(async (request, response) => {
  requests.push(request.url);
  const timing = { path: request.url, start: performance.now(), end: Infinity };
  requestTimes.push(timing);
  // A cold response must still produce visible HTML, not a streamed loader.
  await delay(150);
  timing.end = performance.now();
  response.setHeader('Content-Type', 'application/json');
  const questRequest = new URL(request.url, 'http://backend.test');
  if (questRequest.pathname.startsWith('/quests/119000001/')) {
    const quest = { name: { content: 'A room full of memories' } };
    const choices = ['Ask about the drawing', 'Ask about the cake', 'Stay a little longer'].map(
      (text, index) => ({
        id: `option-${index}`,
        text: { content: text },
        target_line_id: `reply-${index}`,
        branch: {
          line_ids: [`reply-${index}`],
          continuation_line_id: 'shared-return',
        },
      }),
    );
    if (questRequest.pathname.endsWith('/profile')) response.end(JSON.stringify({ quest }));
    else if (questRequest.pathname.endsWith('/transcript'))
      response.end(
        JSON.stringify({
          quest,
          limit: 500,
          lines: [
            {
              id: 'choice-prompt',
              flow_state: 'Room',
              speaker: { label: 'Rover' },
              text: { content: 'There is still so much I want to ask.' },
              player_choices: choices,
            },
            ...[
              'I drew it before I learned what the stars were.',
              'There should be enough for both of us.',
              'Then let us stay here, just for a moment.',
            ].map((text, index) => ({
              id: `reply-${index}`,
              provenance: {source_file: 'BinData/flowState/flowstate.json', source_row: index},
              flow_state: 'Room',
              speaker: { label: 'Denia' },
              text: { content: text },
            })),
            {
              id: 'shared-return',
              flow_state: 'Room',
              speaker: { label: 'Denia' },
              text: { content: 'Shall we see what is outside?' },
            },
            ...[
              'The water remembers everything.',
              'Even the light that never reached the surface.',
              'Some memories move like a dream.',
            ].map((text, index) => ({
              id: `scene-${index}`,
              media: {
                voice_references: [
                  {
                    source: { file: 'PlotAudio fixture' },
                    tracks:
                      index === 1
                        ? [
                            { language: 'en', rover: 'male', url: 'https://cdn.test/male.wav' },
                            { language: 'en', rover: 'female', url: 'https://cdn.test/female.wav' },
                          ]
                        : [{ language: 'en', rover: null, url: 'https://cdn.test/voice.wav' }],
                  },
                ],
              },
              flow_state: 'Memory',
              speaker: { label: 'Denia' },
              text: { content: text },
              illustration:
                index < 2
                  ? {
                      id: 'water-bg',
                      kind: 'image',
                      images: [
                        {
                          rover: null,
                          engine_path: '/Game/Water.Water',
                          url: 'https://cdn.test/water.png',
                        },
                      ],
                    }
                  : {
                      id: 'spine-bg',
                      kind: 'spine',
                      loop: true,
                      resource_id: 850007,
                      images: [],
                      bundle: {
                        url: 'https://cdn.test/scene.zip',
                        size_bytes: 12345678,
                        asset_version: '3.7.0',
                        playback: {format: 'solaris.skeletal-scene.v1', variants: [{rover: null, animation: 'idle', scene_url: 'https://cdn.test/animated-scene.json', textures: {'shuizhong.png':'https://cdn.test/atlas.png'}}], unsupported: []},
                      },
                    },
            })),
          ],
          story_blocks: [],
        }),
      );
    else response.end('null');
    return;
  }
  if (questRequest.pathname.startsWith('/quests/119000000/')) {
    const quest = { name: { content: 'Source-backed interaction fixture' } };
    const state = 'Cave_1_41';
    if (questRequest.pathname.endsWith('/profile')) response.end(JSON.stringify({ quest }));
    else if (questRequest.pathname.endsWith('/transcript'))
      response.end(
        JSON.stringify({
          quest,
          limit: 500,
          lines: [
            {
              id: 'main-line',
              flow_state: 'Main_1_1',
              story_position: [1],
              text: { content: 'Mandatory dialogue' },
            },
            {
              id: 'optional-line',
              flow_state: state,
              action: { index: 1 },
              text: { content: 'Optional fridge dialogue' },
              illustration: {
                id: 'image-1',
                kind: 'image',
                images: [
                  {
                    rover: null,
                    engine_path: '/Game/A.A',
                    url: 'https://cdn.test/story.png',
                  },
                ],
              },
              player_choices: [{ id: 'choice-1', text: { content: 'Leave it here' } }],
            },
          ],
          story_blocks: [
            {
              id: 'explore-room',
              title: 'Explore the room',
              position: [2],
              branches: [],
              flow_states: [],
              source: { file: 'quest.json', key: '119000000_120' },
              interactions: [
                {
                  id: 'e_909_179:0',
                  title: 'Inspect the fridge',
                  flow_states: [{ state }],
                  source: {
                    file: 'client.db',
                    key: '179',
                    raw_path: '$.Options[0]',
                  },
                },
              ],
            },
          ],
        }),
      );
    else if (questRequest.pathname.endsWith('/media'))
      response.end(
        JSON.stringify({
          events: [
            {
              kind: 'sequence',
              action: 'action:optional',
              reference: 'asset:ue:/Game/Scene.Scene',
              flow_state: state,
              action_index: 2,
              engine_path: '/Game/Scene.Scene',
              source: { file: 'flowstate.json', raw_path: '$.Actions[2]' },
            },
          ],
          video_packages: [],
        }),
      );
    else response.end('null');
    return;
  }
  if (questRequest.pathname.startsWith('/nodes/dialogue%3A123')) {
    if (questRequest.pathname.endsWith('/related'))
      return response.end(JSON.stringify({ results: [] }));
    if (questRequest.pathname.endsWith('/narrative-context'))
      return response.end(JSON.stringify({ available: false }));
    return response.end(
      JSON.stringify({
        canonical_key: 'dialogue:123',
        slug: null,
        status: 'active',
        type_id: 1,
      }),
    );
  }
  if (questRequest.pathname === '/story-map') {
    const version = questRequest.searchParams.get('game_version') || null;
    const questType = Number(questRequest.searchParams.get('quest_type_id'));
    const additions = version === '2.1.0' && questType !== 1;
    const title = additions ? 'Silent as a Falling Leaf' : 'First Resonance';
    const owningPatch = version || '1.0.0';
    response.end(
      JSON.stringify({
        selected_game_version: version,
        quest_type_id: questType,
        imported_game_versions: ['1.0.0', '2.1.0'],
        tree_available: false,
        chapters:
          version === '2.1.0' && questType === 1
            ? []
            : [
                {
                  id: 1,
                  title,
                  snapshot_game_version: owningPatch,
                  tree_available: false,
                  nodes: [
                    {
                      id: 10,
                      title,
                      source_kind: 'quest_prerequisite',
                      previous_node_ids: [],
                      quests: [
                        {
                          game_quest_id: 10,
                          title,
                          first_observed_game_version: owningPatch,
                        },
                      ],
                    },
                  ],
                },
              ],
      }),
    );
    return;
  }
  if (
    /^\/quests\/(662686-the-sacred-breeze-so-often-breathes|464283-when-the-night-knocks)\//.test(
      questRequest.pathname,
    )
  ) {
    const title = `Selected quest ${questRequest.searchParams.get('locale')} ${questRequest.searchParams.get('game_version')}`;
    const quest = { name: { content: title } };
    if (questRequest.pathname.endsWith('/profile')) response.end(JSON.stringify({ quest }));
    else if (questRequest.pathname.endsWith('/transcript'))
      response.end(
        JSON.stringify({
          quest,
          lines: [
            {
              id: questRequest.searchParams.get('focus_line') || 'dialogue:1',
              text: { content: `Selected dialogue ${title}` },
            },
          ],
          scenes: [],
        }),
      );
    else response.end('null');
  } else if (request.url === '/characters/character%3A1102/profile?locale=en') {
    response.end(JSON.stringify(profile));
  } else if (request.url === '/characters/character%3A1102/archive?locale=en') {
    response.end(JSON.stringify(archive));
  } else if (request.url === '/items/19875-coriolus/profile?locale=en') {
    response.end(
      JSON.stringify({
        item: { canonical_key: 'item:42300080', name: { content: 'Coriolus' } },
        progression_uses: [4, 8, 12, 16, 20].map((count, index) => ({
          kind: 'character_ascension',
          level_cap: 50 + index * 10,
          required_count: count,
          character: {
            canonical_key: 'character:1203',
            label: 'Lingyang',
            href: '/characters/15511-lingyang',
          },
          source: { file: 'BinData/role_level/rolebreach.json', row: index },
        })),
      }),
    );
  } else if (request.url === '/items/595988-emerald-of-genesis/profile?locale=en') {
    response.end(
      JSON.stringify({
        item: {
          canonical_key: 'item:21020015',
          slug: '595988-emerald-of-genesis',
          name: { content: 'Emerald of Genesis' },
        },
        banner_sources: [
          {
            view_id: 32,
            banner_type: 'permanent_weapon',
            roles: ['preview', 'display'],
            source: {
              file: 'BinData/gacha/gachaviewinfo.json',
              row: 2,
              game_version: '2.0.0',
            },
          },
        ],
        quest_references: [
          {
            quest: testQuest,
            references: [
              {
                interpretation: {
                  label: 'Requires obtaining this item',
                  is_reward: false,
                },
              },
            ],
          },
        ],
      }),
    );
  } else if (request.url === '/quests/139000037/profile?locale=en') {
    response.end(
      JSON.stringify({
        quest: testQuest,
        item_requirements: [
          {
            item_id: 21020015,
            label: 'Requires obtaining this item',
            count: 1,
            item: {
              href: '/items/595988-emerald-of-genesis',
              label: 'Emerald of Genesis',
            },
          },
        ],
      }),
    );
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
          description: {
            content: 'A bright crystal with a stable public address.',
          },
        },
      }),
    );
  } else if (request.url === '/events?server=europe&summary=true') {
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
    if (questRequest.searchParams.get('q') === 'fixture-unavailable') {
      response.statusCode = 503;
      response.end(JSON.stringify({ detail: 'Archive unavailable' }));
      return;
    }
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
  for (const value of [
    'Permanent weapon Convene',
    'Listed in banner preview',
    'Test quest',
    'Requires obtaining this item',
    'not a reward',
  ])
    assert.ok(html.includes(value), value);
  assert.ok(!html.includes('No confirmed acquisition sources recorded yet.'));
  const quest = await fetch(`${site}/quests/139000037`);
  assert.equal(quest.status, 200);
  const questHtml = (await quest.text()).replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '');
  for (const value of [
    'Test quest',
    'Classification evidence',
    '测试21020015武器',
    'Requires obtaining this item',
    'Emerald of Genesis',
  ])
    assert.ok(questHtml.includes(value), value);
});

for (const [slug, version, locale] of [
  ['662686-the-sacred-breeze-so-often-breathes', '2.0.0', 'en'],
  ['464283-when-the-night-knocks', '1.4.0', 'ru'],
  ['662686-the-sacred-breeze-so-often-breathes', '1.4.0', 'uk'],
]) {
  test(`quest SSR and metadata use the requested snapshot for ${slug}`, async () => {
    const start = requests.length;
    const response = await fetch(
      `${site}/quests/${slug}?game_version=${version}&locale=${locale}&line=dialogue%3A999`,
    );
    assert.equal(response.status, 200);
    const html = (await response.text()).replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '');
    assert.ok(html.includes(`<title>Selected quest ${locale} ${version}`));
    assert.ok(html.includes(`Selected dialogue Selected quest ${locale} ${version}`));
    assert.ok(html.includes('data-dialogue-key="dialogue:999"'));
    const calls = requests.slice(start).filter((path) => path.startsWith(`/quests/${slug}/`));
    assert.equal(calls.length, 4, 'metadata and SSR must share the profile request');
    const timings = requestTimes
      .slice(start)
      .filter((entry) => entry.path.startsWith(`/quests/${slug}/`));
    assert.ok(
      Math.max(...timings.map((entry) => entry.start)) <
        Math.min(...timings.map((entry) => entry.end)),
      'all four requests must overlap rather than form an SSR waterfall',
    );
    for (const endpoint of ['profile', 'transcript', 'continuity', 'media']) {
      const call = calls.find((path) => path.startsWith(`/quests/${slug}/${endpoint}?`));
      assert.ok(call, endpoint);
      const query = new URL(call, site).searchParams;
      assert.equal(query.get('locale'), locale);
      assert.equal(query.get('game_version'), version);
      assert.equal(query.get('focus_line'), endpoint === 'transcript' ? 'dialogue:999' : null);
      if (endpoint === 'transcript') {
        assert.equal(query.get('limit'), '2000');
        assert.equal(query.get('include_raw'), 'false');
      }
    }
  });
}

for (const userAgent of ['Mozilla/5.0', 'TelegramBot (like TwitterBot)']) {
  test(`character details and preview are in initial HTML for ${userAgent}`, async () => {
    const start = requests.length;
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
    const calls = requests.slice(start).filter((path) => path.startsWith('/characters/character%3A1102/'));
    // ISR may satisfy a warm read; a cold render must never duplicate either endpoint.
    for (const endpoint of ['profile', 'archive']) {
      assert.ok(calls.filter((path) => path.includes(`/${endpoint}?`)).length <= 1,
        `metadata and page must share the character ${endpoint} read`);
    }
    if (calls.length === 2) {
      const timings = requestTimes.slice(start).filter((entry) => entry.path.startsWith('/characters/character%3A1102/'));
      assert.ok(Math.max(...timings.map((entry) => entry.start)) < Math.min(...timings.map((entry) => entry.end)),
        'character profile and archive must overlap rather than form an SSR waterfall');
    }
    const gallery =
      main.match(/<div class="character-artwork-gallery">([\s\S]*?)<\/figure><\/div>/)?.[1] ?? '';
    assert.ok(gallery.includes(`src="${portrait}"`));
    assert.ok(!gallery.includes(sharedQuestArtwork));
    assert.ok(!gallery.includes('foreign-yangyang'));
    assert.ok(main.includes('Shared portrait reference'));
    assert.ok(main.includes('Shared quest artwork'));
    assert.ok(main.includes(`href="${sharedQuestArtwork}"`));
    assert.doesNotMatch(main, /Awaiting export/);
  });
}

for (const storyType of ['', '&quest_type=9', '&quest_type=1']) {
  test(`story map SSR honors selected patch and path ${storyType || 'all'}`, async () => {
    const response = await fetch(`${site}/story-map?game_version=2.1.0${storyType}`);
    assert.equal(response.status, 200);
    const html = (await response.text()).replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '');
    assert.doesNotMatch(html, /First Resonance|Opening archive record/);
    if (storyType === '&quest_type=1') {
      assert.ok(html.includes('No additions in'));
      assert.ok(requests.includes('/story-map?locale=en&quest_type_id=1&game_version=2.1.0'));
    } else {
      assert.ok(html.includes('Silent as a Falling Leaf'));
      assert.ok(html.includes('game_version=2.1.0'));
      assert.ok(
        requests.includes(
          `/story-map?locale=en&quest_type_id=${storyType ? 9 : 0}&game_version=2.1.0`,
        ),
      );
    }
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

test('node with unavailable narrative renders its source details without a dialogue section', async () => {
  const response = await fetch(`${site}/nodes/dialogue%3A123`);
  assert.equal(response.status, 200);
  const html = (await response.text()).replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '');
  assert.match(html, /dialogue:123/);
  assert.doesNotMatch(html, /Where this line appears|DIALOGUE ENTRY|Opening archive record/);
});

test('optional interaction contains dialogue, choices, pictures and engine scene exactly once', async () => {
  const response = await fetch(`${site}/quests/119000000`);
  assert.equal(response.status, 200);
  const html = (await response.text()).replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '');
  const interaction =
    html.match(/<details><summary>Inspect the fridge<\/summary>([\s\S]*?)client\.db/)?.[1] || '';
  assert.match(interaction, /Optional fridge dialogue/);
  assert.match(interaction, /Leave it here/);
  assert.match(interaction, /src="https:\/\/cdn.test\/story.png"/);
  assert.match(interaction, /rendered by the game engine/);
  assert.equal((html.match(/data-dialogue-key="optional-line"/g) || []).length, 1);
  assert.equal((html.match(/Optional fridge dialogue/g) || []).length, 1);
  assert.match(html, /Mandatory dialogue/);
});

test('quest reader renders alternative paths beside one shared return and one illustrated stage', async () => {
  const response = await fetch(`${site}/quests/119000001`);
  assert.equal(response.status, 200);
  const html = (await response.text()).replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '');
  assert.equal((html.match(/class="quest-fork-path"/g) || []).length, 3);
  assert.equal((html.match(/data-dialogue-key="shared-return"/g) || []).length, 1);
  for (let index = 0; index < 3; index++)
    assert.equal(
      (html.match(new RegExp(`data-dialogue-key="reply-${index}"`, 'g')) || []).length,
      1,
    );
  assert.match(html, /Paths rejoin/);
  assert.equal((html.match(/aria-label="Illustrated dialogue"/g) || []).length, 1);
  assert.match(html, /The water remembers everything/);
  assert.match(html, /Read transcript/);
  assert.match(html, /aria-label="Next dialogue line"/);
});

test('item progression renders five real stages and their total compactly', async () => {
  const response = await fetch(`${site}/items/19875-coriolus`);
  assert.equal(response.status, 200);
  const html = (await response.text()).replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '');
  assert.equal((html.match(/class="progression-level-chip"/g) || []).length, 5);
  const text = html.replace(/<[^>]*>/g, '').replace(/<!--.*?-->/g, '');
  assert.match(text, /Lv\. 50–90/);
  assert.match(text, /60× total/);
  assert.doesNotMatch(text, /845 source entries|10140/);
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
  assert.ok(requests.includes('/events?server=europe&summary=true'));
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

test('announcement failures keep explicit retry addresses and revision selection', async () => {
  const list = await fetch(`${site}/notices?q=fixture-unavailable&offset=24&category=game`);
  const listHtml = (await list.text()).replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '');
  assert.ok(listHtml.includes('The announcement archive could not be loaded.'));
  assert.ok(listHtml.includes('href="/notices?q=fixture-unavailable&amp;offset=24&amp;limit=24&amp;category=game">Try again</a>'));
  const revision = 'b'.repeat(64);
  const detail = await fetch(`${site}/notices/999?revision=${revision}`);
  const detailHtml = (await detail.text()).replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '');
  assert.ok(detailHtml.includes('Announcement unavailable'));
  assert.ok(detailHtml.includes(`href="/notices/999?revision=${revision}">Try again</a>`));
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
  const response = await fetch(`${site}/items/103-astrite`, {
    redirect: 'manual',
  });
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
