import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { skeletonComponents } from "./skeletonComponents.mjs";
const { Skeleton, SkeletonGroup, TextSkeleton } = skeletonComponents;
test("loading groups expose one accessible status and decorative noninteractive bars", () => {
  const html = renderToStaticMarkup(
    React.createElement(
      SkeletonGroup,
      { label: "Loading quests", className: "npc-quest-grid" },
      React.createElement(TextSkeleton, { lines: 3 }),
    ),
  );
  assert.match(html, /role="status"/);
  assert.match(html, /aria-busy="true"/);
  assert.match(html, /class="ui-skeleton-label">Loading quests/);
  assert.equal((html.match(/class="ui-skeleton "/g) || []).length, 3);
  assert.match(html, /aria-hidden="true"/);
  assert.doesNotMatch(html, /<button|<a\b|tabindex/i);
});
test("skeleton geometry accepts card-owned sizes and motion respects system preference", () => {
  const html = renderToStaticMarkup(
    React.createElement(Skeleton, {
      className: "portrait",
      style: { height: 180 },
    }),
  );
  assert.match(html, /portrait/);
  assert.match(html, /height:180px/);
  const css = readFileSync(
    new URL("../src/styles/index.css", import.meta.url),
    "utf8",
  );
  assert.match(
    css,
    /@media \(prefers-reduced-motion: reduce\)\s*\{\s*\.ui-skeleton\s*\{\s*animation: none/,
  );
  assert.match(css, /\.ui-skeleton-label[^}]*clip-path: inset\(50%\)/);
});
