import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

test("cutscene player follows branches and respects clip boundaries", async () => {
  class Element {
    constructor() {
      this.events = {};
      this.children = [];
      this.hidden = false;
      this.value = "1";
      this.checked = true;
    }
    addEventListener(name, fn) {
      this.events[name] = fn;
    }
    dispatch(name) {
      this.events[name]?.({ preventDefault() {} });
    }
    append(child) {
      this.children.push(child);
    }
    replaceChildren() {
      this.children = [];
    }
  }
  const status = new Element(),
    video = new Element(),
    caption = new Element(),
    choice = new Element();
  const prompt = new Element(),
    buttons = new Element(),
    stage = new Element(),
    controls = new Element();
  const context = new Element(),
    language = new Element(),
    music = new Element(),
    subtitles = new Element();
  const timeline = new Element(),
    decisions = new Element(),
    restart = new Element(),
    fullscreen = new Element();
  language.value = "en";
  video.paused = true;
  video.currentTime = 0;
  video.duration = 10;
  video.volume = 1;
  video.playbackRate = 1;
  video.pause = function () {
    this.paused = true;
    this.dispatch("pause");
  };
  video.play = function () {
    this.paused = false;
    this.dispatch("play");
    return Promise.resolve();
  };
  choice.querySelector = (selector) => (selector === "p" ? prompt : buttons);
  stage.querySelector = (selector) =>
    ({ video, ".sa-cutscene-caption": caption, ".sa-cutscene-choice": choice })[
      selector
    ];
  const form = new Element();
  form.elements = { language, version: { value: "" } };
  const root = new Element();
  root.dataset = {
    url: "https://api.solarisatlas.fun/console/cutscene-data/15536",
    name: "Start",
  };
  const elements = {
    form,
    ".sa-cutscene-status": status,
    ".sa-cutscene-stage": stage,
    ".sa-cutscene-controls": controls,
    ".sa-cutscene-context": context,
    "[name=music]": music,
    "[name=captions]": subtitles,
    "[name=timeline]": timeline,
    ".sa-cutscene-decisions": decisions,
    "[data-action=restart]": restart,
    "[data-action=fullscreen]": fullscreen,
  };
  root.querySelector = (selector) => elements[selector];
  const flow = {
    entry: "choice",
    nodes: [
      {
        id: "choice",
        kind: "choice",
        options: [
          { label: "A", next: "a" },
          { label: "B", next: "b" },
        ],
      },
      { id: "a", kind: "clip", asset: "a", start: 0, end: 2, next: "b" },
      { id: "b", kind: "clip", asset: "b", start: 0, end: 3, next: null },
    ],
    media: { a: { url: "/a" }, b: { url: "/b" } },
  };
  const document = {
    getElementById: () => root,
    createElement: () => new Element(),
  };
  const requests = [];
  const fetch = async (url) => {
    requests.push(url);
    return {
    ok: true,
    json: async () => ({ game_version: "1.0", event: { playback: flow } }),
    };
  };
  vm.runInNewContext(
    readFileSync(
      new URL(
        "../src/wuwa_story/admin/statics/cutscene-player.js",
        import.meta.url,
      ),
      "utf8",
    ),
    { document, fetch, URLSearchParams, AbortController, Audio: Element },
  );
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(requests[0], "https://api.solarisatlas.fun/console/cutscene-data/15536?locale=en");
  assert.equal(buttons.children.length, 2);
  buttons.children[0].dispatch("click");
  assert.equal(video.src, "/a");
  video.onloadedmetadata();
  assert.equal(Number(timeline.max), 5);
  video.currentTime = 2;
  video.dispatch("timeupdate");
  assert.equal(video.src, "/b");
  assert.equal(decisions.children.length, 1);
  decisions.children[0].dispatch("click");
  buttons.children[1].dispatch("click");
  assert.equal(video.src, "/b");
  assert.equal(Number(timeline.max), 3);
  form.elements.version.value = "1.0.0";
  language.value = "ja";
  form.dispatch("submit");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(requests.at(-1), "https://api.solarisatlas.fun/console/cutscene-data/15536?locale=ja&game_version=1.0.0");
});
