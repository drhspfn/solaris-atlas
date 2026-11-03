import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

const require = createRequire(import.meta.url);
function component(url) {
  const source = readFileSync(url, 'utf8');
  const compiled = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
  } }).outputText;
  const module = { exports: {} };
  const localRequire = (name) => {
    if (name === '@/lib/router') return { Link: ({ to, children, ...props }) => React.createElement('a', { href: to, ...props }, children) };
    if (name === './ExplanationSources') return component(new URL('../src/components/story/ExplanationSources.tsx', import.meta.url));
    return require(name);
  };
  new Function('require', 'module', 'exports', compiled)(localRequire, module, module.exports);
  return module.exports;
}
const { ExplanationWorkspace } = component(new URL('../src/components/story/ExplanationWorkspace.tsx', import.meta.url));
const render = (explanation) => renderToStaticMarkup(React.createElement(ExplanationWorkspace, { explanation }));

test('legacy explanations do not acquire empty workspace sections', () => {
  assert.equal(render({ nodes: [] }), '');
});

test('priority questions precede story sections and later answers remain closed spoilers', () => {
  const html = render({ nodes: [], sections: [{ key: 'arrival', title: 'Arrival', description: 'The traveller reaches the city.',
    start_node_id: 7, end_node_id: 8, citations: [{ node_id: 7, quote: 'Welcome', href: '/quests/1?line=dialogue%3A7' }] }],
    questions: [
      { id: 1, question: 'Low priority', priority: 3, status: 'open', resolution: null },
      { id: 2, question: 'Who sent the traveller?', priority: 0, status: 'resolved', resolution: {
        explanation: 'The answer appears in the next quest.', citations: [{ node_id: 9, quote: 'I sent them.', href: '/nodes/dialogue%3A9' }],
      } },
    ] });
  assert.ok(html.indexOf('Who sent') < html.indexOf('Low priority'));
  assert.ok(html.indexOf('Questions and discoveries') < html.indexOf('Inside this quest'));
  assert.match(html, /<details><summary>Later discovery · contains spoilers<\/summary>/);
  assert.doesNotMatch(html, /<details open/);
  assert.match(html, /href="\/quests\/1\?line=dialogue%3A7"/);
  assert.match(html, /href="\/nodes\/dialogue%3A9"/);
});
