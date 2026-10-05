import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';

let vite, ActionMenu, AnalysisActions, AuthProvider;
before(async () => {
  vite = await createServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom' });
  ({ ActionMenu } = await vite.ssrLoadModule('/src/components/ui/ActionMenu.tsx'));
  ({ AnalysisActions } = await vite.ssrLoadModule('/src/components/story/AnalysisActions.tsx'));
  ({ AuthProvider } = await vite.ssrLoadModule('/src/auth/AuthProvider.tsx'));
});
after(async () => vite?.close());
test('action disclosure starts closed and has a named native keyboard trigger', () => {
  const html = renderToStaticMarkup(
    createElement(
      ActionMenu,
      { label: 'Quest actions' },
      createElement('button', { type: 'button' }, 'Analyze quest'),
    ),
  );
  assert.match(html, /<details class="action-menu">/);
  assert.match(html, /<summary aria-label="Quest actions"/);
  assert.ok(!html.includes(' open=""'));
});
test('paid analysis actions are not rendered without an administrator session', () => {
  const html = renderToStaticMarkup(
    createElement(
      AuthProvider,
      null,
      createElement(AnalysisActions, { target: { kind: 'quest', questId: 1 } }),
    ),
  );
  assert.equal(html, '');
});
