import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';

let vite, PlayerText;
before(async () => {
  vite = await createServer({ server: { middlewareMode: true }, appType: 'custom' });
  ({ PlayerText } = await vite.ssrLoadModule('/src/components/dialogue/PlayerText.tsx'));
});
after(async () => {
  await vite?.close();
});
const render = (value, fallback = '') =>
  renderToStaticMarkup(
    createElement(PlayerText, {
      value,
      fallback,
      display: { name: 'Jenya', colorMode: 'custom', customColor: '#abcdef' },
    }),
  );

test('uses verified game palette for dialogue and speaker names', () => {
  assert.equal(
    render('<color=XinyuehuSub>Run, Hsin... Run faster.</color>'),
    '<span style="color:#FDB45DFF">Run, Hsin... Run faster.</span>',
  );
  assert.equal(
    render({ content: '<color=XinyuehuTitle>Thoughts</color>' }),
    '<span style="color:#FD965DFF">Thoughts</span>',
  );
});

test('annotations retain the spoken word rather than the annotation argument', () => {
  assert.equal(
    render('If it had been you, <ano=Xuanqing>she</ano> might still be here.'),
    'If it had been you, she might still be here.',
  );
});
test('nested wrappers preserve player replacement including speaker-only labels', () => {
  const name = render('{PlayerName}');
  assert.match(name, /class="narrative-player-name"/);
  assert.match(name, /color:#abcdef/);
  assert.match(name, />Jenya<\/span>/);
  assert.equal(
    render('<color=XinyuehuTitle><ano=Rover>{PlayerName}</ano></color>'),
    `<span style="color:#FD965DFF">${name}</span>`,
  );
  assert.equal(render(null, '<color=X>{PlayerName}</color>'), name);
});
test('unknown markup and player input remain escaped, not executable HTML', () => {
  assert.equal(
    render('2 < 3 <img src=x onerror=alert(1)>'),
    '2 &lt; 3 &lt;img src=x onerror=alert(1)&gt;',
  );
  const html = renderToStaticMarkup(
    createElement(PlayerText, {
      value: '{PlayerName}',
      display: { name: '<img src=x>', colorMode: 'default', customColor: '' },
    }),
  );
  assert.ok(html.includes('&lt;img src=x&gt;'));
  assert.ok(!html.includes('<img'));
});

test('nested colors restore the parent; unknown styles and CSS are not executed', () => {
  assert.equal(
    render('<color="#abc">A<color=#12345678>B</color>C</color>D'),
    '<span style="color:#abc">A</span><span style="color:#12345678">B</span><span style="color:#abc">C</span>D',
  );
  assert.equal(render('<color=Unknown>text</color>'), 'text');
  assert.equal(render('<color=red;background:url(https://example.com)>text</color>'), 'text');
  assert.equal(render('<color=constructor>text</color>'), 'text');
  assert.equal(render('</color>plain'), 'plain');
});
