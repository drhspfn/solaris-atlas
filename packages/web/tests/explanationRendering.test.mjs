import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import { createServer } from 'vite';

import { storyExplanation } from './fixtures/storyExplanation.mjs';

let vite;
let ExplanationBlocks;
before(async () => {
  vite = await createServer({ server: { middlewareMode: true }, appType: 'custom' });
  ({ ExplanationBlocks } = await vite.ssrLoadModule('/src/components/story/ExplanationBlocks.tsx'));
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
