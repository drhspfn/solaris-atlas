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
    append(...children) {
      this.children.push(...children);
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
  const previews = new Element(), publicDescriptions = new Element(), system = new Element();
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
    ".sa-cutscene-previews": previews,
    ".sa-cutscene-public": publicDescriptions,
    ".sa-cutscene-system": system,
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
  let payload = { game_version: "1.0", event: { playback: flow },
    previews: [{url:"https://cdn.test/preview.jpg"},{url:"https://cdn.test/preview.jpg"},{url:"javascript:alert(1)"}],
    public_descriptions: [{title:"Public scene",text:"A compact public description."}],
    system_reports: [{asset_node_id:10,title:"Observed scene",events:[{time:0,observation:"The shore."},{time:65,observation:"<script>plain text</script>"}]}],
  };
  const fetch = async (url) => {
    requests.push(url);
    return {
    ok: true,
    json: async () => payload,
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
  assert.equal(previews.children.length, 1);
  assert.equal(previews.children[0].children[0].src, "https://cdn.test/preview.jpg");
  assert.equal(publicDescriptions.children[0].children[1].textContent, "A compact public description.");
  assert.equal(system.children[0].children[1].children.length, 2);
  assert.equal(system.children[0].children[1].children[1].children[0].textContent, "1:05");
  assert.equal(system.children[0].children[1].children[1].children[1].textContent, "<script>plain text</script>");
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
  payload = { previews: [], public_descriptions: [], system_reports: [payload.system_reports[0]], event: {playback:null} };
  form.dispatch("submit");
  await new Promise((resolve) => setImmediate(resolve));
  assert.match(publicDescriptions.children[0].textContent, /No public summary/);
  assert.match(previews.children[0].textContent, /No preview images/);
  assert.equal(system.children[0].children[1].children.length, 2);
  assert.equal(stage.hidden, true);
});
