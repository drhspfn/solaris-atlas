import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import { createServer } from 'vite';

import { adaptiveExplanation, storyExplanation } from './fixtures/storyExplanation.mjs';

let vite;
let ExplanationBlocks;
let ExplanationContext;
let ExplanationFollowUps;
before(async () => {
  vite = await createServer({ server: { middlewareMode: true }, appType: 'custom' });
  ({ ExplanationBlocks } = await vite.ssrLoadModule('/src/components/story/ExplanationBlocks.tsx'));
  ({ ExplanationContext, ExplanationFollowUps } = await vite.ssrLoadModule(
    '/src/components/story/ExplanationContext.tsx',
  ));
});
after(async () => {
  await vite?.close();
});
const render = (explanation) =>
  renderToStaticMarkup(
    createElement(MemoryRouter, null, createElement(ExplanationBlocks, { explanation })),
  );

test('claims show separate chronology and knowledge with spoilers closed', () => {
  const html = render(storyExplanation);
  for (const label of [
    'Unresolved',
    'Observed anomaly',
    'Encountered in quest',
    'World chronology',
    'Time unknown',
    'Known at this point',
    'Baizhi notes a clear airway and dry clothing',
  ])
    assert.ok(html.includes(label), label);
  assert.match(html, /<details class="assertion-resolution"><summary>/);
  assert.ok(!html.includes('href="#"'));
});

test('legacy explanations do not acquire invented confidence or chronology', () => {
  const block = {
    ...storyExplanation.blocks[0],
    assertions: undefined,
    related_records: undefined,
    related_node_ids: [1],
  };
  const html = render({ ...storyExplanation, blocks: [block] });
  assert.ok(html.includes('Baizhi’s examination'));
  assert.ok(!html.includes('Known at this point'));
  assert.ok(!html.includes('Confirmed'));
});

test('source disclosures identify their own patch version', () => {
  const block = {
    ...storyExplanation.blocks[0],
    citations: storyExplanation.blocks[0].citations.map((citation) => ({
      ...citation,
      game_version: '2.1.0',
      snapshot_id: 42,
    })),
  };
  assert.ok(render({ ...storyExplanation, blocks: [block] }).includes('2.1.0'));
});

test('adaptive knowledge, corpus limits and supplemental spoilers remain separate', () => {
  const html = renderToStaticMarkup(
    createElement(
      MemoryRouter,
      null,
      createElement(ExplanationContext, { explanation: adaptiveExplanation }),
      createElement(ExplanationFollowUps, { explanation: adaptiveExplanation }),
    ),
  );
  for (const text of [
    'main plot',
    'What this quest establishes',
    'Cannot conclude',
    'Open threads',
    'Flagged for review',
    'New context · spoilers',
    'high priority after review',
  ])
    assert.ok(html.includes(text), text);
  assert.ok(!html.includes('<details open'));
  assert.ok(
    !html.includes(`<summary>New context · spoilers · ${adaptiveExplanation.supplements[0].title}`),
  );
  assert.ok(!html.includes('href="#"'));
});

test('choice occurrence and character speculation cannot look like unconditional facts', () => {
  const fixture = structuredClone(storyExplanation);
  const claim = fixture.blocks[0].assertions[0];
  claim.status = 'character_speculation';
  claim.occurrence = 'player_choice';
  claim.condition = 'Only when this answer is selected';
  const html = render(fixture);
  assert.ok(html.includes('Character speculation'));
  assert.ok(html.includes('player choice'));
  assert.ok(html.includes(claim.condition));
  assert.ok(html.includes('in the loaded corpus'));
});

test('unknown occurrence is disclosed once and secondary functions keep their own role', () => {
  const fixture = structuredClone(adaptiveExplanation);
  fixture.assessment.secondary_functions = ['region_lore', 'Sentinel_arc'];
  fixture.blocks
    .flatMap((block) => block.assertions || [])
    .forEach((claim) => {
      claim.occurrence = 'unknown';
    });
  const html = renderToStaticMarkup(
    createElement(
      MemoryRouter,
      null,
      createElement(ExplanationContext, { explanation: fixture }),
      createElement(ExplanationBlocks, { explanation: fixture }),
    ),
  );
  assert.equal((html.match(/Occurrence of some source passages/g) || []).length, 1);
  assert.ok(!html.includes('Branch occurrence not established'));
  assert.ok(html.includes('Secondary functions'));
  assert.ok(html.includes('main plot'));
});
