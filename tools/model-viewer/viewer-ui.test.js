import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { join } from "node:path";

// Optional browser checks: NODE_PATH may point to the desktop's bundled Playwright.
// No runtime dependency is added to this static tool.
let chromium;
try {
  ({ chromium } = createRequire(import.meta.url)("playwright"));
} catch {
  /* browser tooling optional */
}

test("static UI preserves the scene integration IDs without native select controls", async () => {
  const html = await readFile(new URL("./index.html", import.meta.url), "utf8");
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map((match) => match[1]);
  assert.equal(new Set(ids).size, ids.length);
  for (const id of [
    "viewport",
    "status",
    "retry",
    "left",
    "right",
    "closer",
    "farther",
    "reset",
    "floor",
    "turntable",
    "lock-position",
    "lock-axes",
    "lock-x",
    "lock-y",
    "lock-z",
    "lock-note",
    "physics",
    "strength",
    "strength-value",
    "physics-state",
    "physics-note",
    "play",
    "time",
    "duration",
    "playback",
    "load-animations",
    "animation-status",
    "axis-key",
    "animations",
    "animation-previous",
    "animation-next",
    "speed",
    "camera-bone",
    "camera-follow",
    "camera-space",
    "camera-x",
    "camera-y",
    "camera-z",
    "camera-pause",
    "camera-focus",
    "camera-attach",
    "camera-detach",
    "camera-state",
    "fullscreen",
  ])
    assert.ok(ids.includes(id), id);
  assert.doesNotMatch(html, /<select\b/i);
});

test(
  "pickers, modal tabs, state replacement, and mobile/preview layouts",
  {
    skip:
      !chromium && "Playwright unavailable; set NODE_PATH to browser tooling",
  },
  async (t) => {
    const server = createServer(async (req, res) => {
      const name = req.url === "/" ? "index.html" : req.url.slice(1);
      if (!["index.html", "viewer-ui.js", "viewer.css"].includes(name)) {
        res.writeHead(404).end();
        return;
      }
      let content = await readFile(
        new URL(`./${name}`, import.meta.url),
        "utf8",
      );
      // UI fixtures intentionally exercise no Three scene, model, or camera code.
      if (name === "index.html")
        content = content.replace(
          /<script type="module" src="\.\/viewer.js"><\/script>/,
          "",
        );
      res.setHeader(
        "Content-Type",
        name.endsWith(".js")
          ? "text/javascript"
          : name.endsWith(".css")
            ? "text/css"
            : "text/html",
      );
      res.end(content);
    });
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    t.after(() => {
      server.closeAllConnections();
      return new Promise((resolve) => server.close(resolve));
    });
    const browser = await chromium.launch({
      headless: true,
      ...(process.env.VIEWER_TEST_BROWSER
        ? { executablePath: process.env.VIEWER_TEST_BROWSER }
        : {}),
    });
    t.after(() => browser.close());
    const page = await browser.newPage({
      viewport: { width: 1100, height: 800 },
    });
    page.setDefaultTimeout(7000);
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await page.evaluate(async () => {
      const { createViewerUI } = await import("./viewer-ui.js");
      window.ui = createViewerUI();
      window.choices = [];
      window.boneChoices = [];
      window.speedChanges = [];
      document.getElementById("speed").onchange = (event) =>
        window.speedChanges.push(event.target.value);
      window.clips = [
        {
          name: "Stand1",
          userData: {
            catalog: {
              category: "Locomotion",
              group: "Idle",
              subgroup: "Standing",
              label: "Quiet stance",
            },
          },
        },
        {
          name: "Run",
          userData: {
            catalog: {
              category: "Locomotion",
              group: "Movement",
              label: "Run forward",
            },
          },
        },
        {
          name: "Attack",
          userData: {
            catalog: {
              category: "Combat",
              group: "Basic",
              label: "Basic attack",
            },
          },
        },
        { name: "<img src=x onerror=alert(1)>" },
      ];
      window.ui.setAnimations(
        window.clips,
        (name) => window.choices.push(name),
        "Run",
      );
      window.ui.setBones(
        [
          { value: "Head", label: "Head", group: "Upper body" },
          { value: "Root", label: "Root" },
        ],
        (value) => window.boneChoices.push(value),
      );
      document.getElementById("playback").disabled = false;
      if (window.ui !== createViewerUI()) throw new Error("Not idempotent");
    });
    const selected = id => page.locator(id).textContent();
    const menu = id => page.locator(id);
    const item = (id, name) => menu(id).getByRole('menuitem', { name });
    const leaf = (id, name) => menu(id).getByRole('menuitemradio', { name, exact: true });
    const popup = () => page.locator('.picker-popup:popover-open');
    assert.equal(await selected('#animation-selection'), 'Run forward');
    assert.deepEqual(await page.evaluate(() => window.choices), []);
    await page.locator('#animation-picker').click();
    assert.equal(await page.getByRole('searchbox').count(), 0);
    assert.equal(await menu('#animation-list').getByRole('menuitemradio').count(), 0);
    assert.equal(await menu('#animation-list').getByRole('menuitem').count(), 3);
    assert.equal(await page.evaluate(() => document.activeElement.textContent), 'Locomotion2  ›');
    if (process.env.VIEWER_UI_SCREENSHOT_DIR) await page.screenshot({ path: join(process.env.VIEWER_UI_SCREENSHOT_DIR, 'viewer-desktop.png') });

    // Drill down by keyboard; every level replaces the previous rows.
    await page.keyboard.press('Enter');
    assert.equal(await item('#animation-list', /^Locomotion/).count(), 0);
    assert.equal(await item('#animation-list', /^Idle/).count(), 1);
    await page.keyboard.press(' ');
    assert.equal(await item('#animation-list', /^Standing/).count(), 1);
    await page.keyboard.press('ArrowRight');
    assert.equal(await leaf('#animation-list', 'Quiet stance').count(), 1);
    assert.equal(await menu('#animation-list').locator('button[tabindex="0"]').count(), 1);
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('#animations').inputValue(), 'Stand1');
    assert.deepEqual(await page.evaluate(() => window.choices), ['Stand1']);
    assert.equal(await page.evaluate(() => document.activeElement.id), 'animation-picker');

    // Opening again retains the selected subgroup, instead of returning to root.
    await page.locator('#animation-picker').click();
    assert.equal(await leaf('#animation-list', 'Quiet stance').count(), 1);
    assert.equal(await menu('#animation-list').getByRole('menuitem').count(), 0);
    await page.keyboard.press('Escape');
    await page.keyboard.press('Escape');
    await page.keyboard.press('Escape');
    await page.keyboard.press('Escape');

    // Escape/Left/Back return one level and restore the row that opened it.
    await page.locator('#animation-picker').press('ArrowDown');
    await item('#animation-list', /^Locomotion/).click();
    await item('#animation-list', /^Movement/).click();
    await page.keyboard.press('Escape');
    assert.equal(await page.evaluate(() => document.activeElement.getAttribute('aria-label')), 'Movement, 1 options');
    await page.keyboard.press('ArrowLeft');
    assert.equal(await item('#animation-list', /^Combat/).count(), 1);
    await item('#animation-list', /^Combat/).click();
    await popup().getByRole('button', { name: 'Back to Animations', exact: true }).click();
    assert.equal(await page.evaluate(() => document.activeElement.getAttribute('aria-label')), 'Combat, 1 options');
    await page.keyboard.press('Home');
    await page.keyboard.press('ArrowUp');
    assert.equal(await page.evaluate(() => document.activeElement.getAttribute('aria-label')), 'Other animations, 1 options');
    await page.keyboard.press('ArrowDown');
    assert.equal(await page.evaluate(() => document.activeElement.getAttribute('aria-label')), 'Locomotion, 2 options');
    await page.keyboard.press('End');
    await page.keyboard.press('Home');

    // Search is opt-in, global, safe text, with clear and Back to the prior hierarchy.
    await item('#animation-list', /^Combat/).click();
    await popup().getByRole('button', { name: 'Search animations', exact: true }).click();
    const search = page.getByRole('searchbox', { name: 'Search all animations' });
    assert.equal(await search.evaluate(node => document.activeElement === node), true);
    await search.fill('standing');
    assert.equal(await menu('#animation-list').getByRole('menuitemradio').count(), 1);
    assert.match(await menu('#animation-list').textContent(), /Locomotion \/ Idle \/ Standing/);
    await search.press('Enter');
    assert.deepEqual(await page.evaluate(() => window.choices), ['Stand1', 'Stand1']);
    await page.locator('#animation-picker').click();
    await popup().getByRole('button', { name: 'Search animations', exact: true }).click();
    await search.fill('no such clip');
    assert.match(await menu('#animation-list').textContent(), /No matches/);
    await search.press('Enter');
    assert.equal(await search.isVisible(), true);
    await popup().getByRole('button', { name: 'Clear animations search' }).click();
    assert.equal(await search.inputValue(), '');
    assert.equal(await search.evaluate(node => document.activeElement === node), true);
    assert.equal(await menu('#animation-list').getByRole('menuitemradio').count(), 4);
    assert.equal(await menu('#animation-list').locator('img').count(), 0);

    // IME input and navigation keys must not filter/select/go back mid-composition.
    await search.evaluate(node => {
      node.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }));
      node.value = 'Run';
      node.dispatchEvent(new InputEvent('input', { bubbles: true, isComposing: true }));
      node.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', isComposing: true, bubbles: true }));
      node.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', isComposing: true, bubbles: true }));
    });
    assert.equal(await menu('#animation-list').getByRole('menuitemradio').count(), 4);
    assert.equal(await search.isVisible(), true);
    await search.evaluate(node => node.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true })));
    assert.equal(await menu('#animation-list').getByRole('menuitemradio').count(), 1);
    await search.press('ArrowDown');
    await page.keyboard.press(' ');
    assert.equal(await selected('#animation-selection'), 'Run forward');
    await page.evaluate(() => window.ui.selectAnimation('Attack'));
    assert.equal(await selected('#animation-selection'), 'Basic attack');
    assert.deepEqual(await page.evaluate(() => window.choices), ['Stand1', 'Stand1', 'Run']);
    await page.locator('#animation-previous').click();
    assert.equal(await selected('#animation-selection'), 'Run forward');
    assert.equal(await page.evaluate(() => window.choices.at(-1)), 'Run');
    await page.locator('#animation-picker').click();
    assert.equal(await leaf('#animation-list', 'Run forward').count(), 1);
    await page.locator('h1').click();
    await page.locator('#animation-next').click();
    assert.equal(await selected('#animation-selection'), 'Basic attack');
    assert.equal(await page.evaluate(() => window.choices.at(-1)), 'Attack');

    await page.locator('#speed-picker').click();
    await page.keyboard.press('End');
    await page.keyboard.press('Enter');
    assert.deepEqual(await page.evaluate(() => window.speedChanges), ['2']);
    await page.locator('#settings-open').click();
    await page.locator('#tab-scene').focus();
    await page.locator('#tab-scene').press('End');
    assert.equal(await page.locator('#tab-camera').getAttribute('aria-selected'), 'true');
    assert.equal(await page.locator('#panel-scene').isVisible(), false);
    await page.locator('#camera-bone').click();
    assert.equal(await page.getByRole('searchbox').count(), 0);
    assert.equal(await menu('#bone-list').getByRole('menuitemradio').count(), 0);
    await item('#bone-list', /^Upper body/).click();
    await leaf('#bone-list', 'Head').click();
    assert.deepEqual(await page.evaluate(() => window.boneChoices), ['Head']);
    await page.evaluate(() => window.ui.selectBone('Root'));
    assert.equal(await page.locator('#camera-bone').evaluate(node => node.value), 'Root');
    assert.deepEqual(await page.evaluate(() => window.boneChoices), ['Head']);
    await page.locator('#camera-bone').click();
    await item('#bone-list', /^Upper body/).click();
    await popup().getByRole('button', { name: 'Search bones', exact: true }).click();
    const boneSearch = page.getByRole('searchbox', { name: 'Search all bones' });
    await boneSearch.fill('Root');
    assert.equal(await menu('#bone-list').getByRole('menuitemradio').count(), 1);
    await boneSearch.press('Escape');
    assert.equal(await leaf('#bone-list', 'Head').count(), 1);
    assert.equal(await page.getByRole('searchbox').count(), 0);
    await page.keyboard.press('Escape'); // Upper body -> root
    await page.keyboard.press('Escape'); // root -> close popover
    assert.equal(await page.locator('#viewer-settings').evaluate(node => node.open), true);
    assert.equal(await page.evaluate(() => document.activeElement.id), 'camera-bone');
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#viewer-settings').evaluate(node => node.open), false);
    assert.equal(await page.evaluate(() => document.activeElement.id), 'settings-open');
    await page.locator('#animation-picker').click();
    await page.locator('h1').click();
    assert.equal(await page.locator('#animation-picker').getAttribute('aria-expanded'), 'false');
    await page.locator('#animation-picker').click();
    await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(() => document.activeElement.id), 'play');
    assert.equal(await page.locator('#animation-picker').getAttribute('aria-expanded'), 'false');

    // Constrain the picker itself; the parent owns settings sheet positioning.
    await page.setViewportSize({ width: 360, height: 640 });
    await page.locator('#settings-open').click();
    await page.locator('#camera-bone').click();
    const popupBounds = await popup().boundingBox();
    if (process.env.VIEWER_UI_SCREENSHOT_DIR) await page.screenshot({ path: join(process.env.VIEWER_UI_SCREENSHOT_DIR, 'viewer-mobile.png') });
    assert.ok(popupBounds.x >= 0 && popupBounds.x + popupBounds.width <= 360);
    await page.keyboard.press('Escape');
    await page.locator('#settings-close').click();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);

    // Replacing data while drilling/searching drops stale hierarchy and callbacks.
    await page.locator('#animation-picker').click();
    await page.keyboard.press('Escape'); // Basic -> Combat
    await page.keyboard.press('Escape'); // Combat -> root
    await item('#animation-list', /^Locomotion/).click();
    await page.evaluate(() => window.ui.setAnimations([{ name: 'Replacement' }], name => window.choices.push(name)));
    assert.equal(await page.locator('#animation-picker').getAttribute('aria-expanded'), 'false');
    await page.locator('#animation-picker').click();
    await item('#animation-list', /^Other animations/).click();
    await leaf('#animation-list', 'Replacement').click();
    assert.equal(await selected('#animation-selection'), 'Replacement');
    assert.equal(await page.locator('#animation-previous').isDisabled(), true);
    assert.equal(await page.locator('#animation-next').isDisabled(), true);
    await page.evaluate(() => {
      window.ui.setAnimations([], () => {});
      window.ui.setBones([], () => {});
    });
    assert.equal(await page.locator('#animation-picker').isDisabled(), true);
    assert.equal(await page.locator('#animation-previous').isDisabled(), true);
    assert.equal(await page.locator('#animation-next').isDisabled(), true);
    assert.equal(await selected('#animation-selection'), 'No animations loaded');
    assert.equal(await page.locator('#camera-attach').isDisabled(), true);
    assert.equal(await page.locator('#camera-focus').isDisabled(), true);
    await page.evaluate(() => { document.body.dataset.mode = 'preview'; });
    assert.equal(await page.locator('.viewer-header').isVisible(), false);
    assert.equal(await page.locator('.viewer-footer').isVisible(), false);
    assert.equal(await page.locator('#viewport').isVisible(), true);
    assert.equal(await page.locator('body').evaluate(node => getComputedStyle(node).backgroundColor), 'rgba(0, 0, 0, 0)');
    assert.deepEqual(errors, []);
  },
);
