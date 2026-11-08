import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

function setup() {
  class Element {
    constructor() {
      this.events = {};
      this.dataset = {};
      this.value = "";
      this.disabled = false;
      this.checked = false;
      this.innerHTML = "";
      this.textContent = "";
      this.elements = {
        interval_seconds: { value: "3", disabled: false },
        sampling_mode: {
          value: "full",
          addEventListener(type, fn) {
            this[type] = fn;
          },
        },
        start_seconds: { value: "" },
        end_seconds: { value: "" },
        timestamp_seconds: { value: "" },
        frame_offset: { value: "0" },
      };
    }
    addEventListener(type, fn) {
      this.events[type] = fn;
    }
    setAttribute() {}
    removeAttribute() {}
    replaceChildren() {}
    pause() {}
    load() {}
    focus() {}
    getAttribute() {
      return this.src || null;
    }
    querySelector() {
      return new Element();
    }
    getBoundingClientRect() {
      return { left: 0, top: 0, width: 100, height: 100 };
    }
    setPointerCapture() {}
  }
  const elements = new Map();
  const root = {
    querySelector(s) {
      if (!elements.has(s)) elements.set(s, new Element());
      return elements.get(s);
    },
    querySelectorAll() {
      return [];
    },
  };
  const requests = [],
    messages = [],
    timers = [];
  const api = (path, body, method) =>
    new Promise((resolve, reject) =>
      requests.push({ path, body, method, resolve, reject }),
    );
  const body = new Element(),
    children = new Map();
  body.querySelector = (s) => {
    if (!children.has(s)) children.set(s, new Element());
    return children.get(s);
  };
  let saveDialog,
    dialogHtml,
    closed = false,
    outerBusy = false;
  const context = {
    URL,
    URLSearchParams,
    location: { href: "http://test/" },
    setTimeout: (fn) => {
      timers.push(fn);
      return timers.length;
    },
    clearTimeout() {},
    Image: class {},
    document: { querySelector: () => body },
  };
  vm.runInNewContext(
    readFileSync(
      new URL(
        "../src/wuwa_story/admin/statics/bbox-annotation.js",
        import.meta.url,
      ),
      "utf8",
    ),
    context,
  );
  vm.runInNewContext(
    readFileSync(
      new URL(
        "../src/wuwa_story/admin/statics/video-review.js",
        import.meta.url,
      ),
      "utf8",
    ),
    context,
  );
  const review = context.createVisualMemoryVideoReview({
    root,
    api,
    escape: String,
    message(text, error) {
      messages.push({ text, error });
    },
    chooseLinks() {},
    dialog(title, html, save) {
      dialogHtml = html;
      saveDialog = save;
      closed = false;
    },
    closeDialog() {
      closed = true;
    },
    isBusy() {
      return outerBusy;
    },
  });
  const element = (s) => root.querySelector(s);
  const tick = () => new Promise((resolve) => setImmediate(resolve));
  return {
    review,
    element,
    requests,
    timers,
    tick,
    body,
    messages,
    save: () => saveDialog(),
    set busy(value) {
      outerBusy = value;
    },
    get html() {
      return dialogHtml;
    },
    get closed() {
      return closed;
    },
  };
}

test("catalog ignores stale searches", async () => {
  const t = setup();
  t.review.open();
  t.element("#vm-video-search [name=q]").value = "new";
  t.element("#vm-video-search").events.submit({ preventDefault() {} });
  t.requests[1].resolve({ items: [{ name: "New" }], total: 1 });
  await t.tick();
  t.requests[0].resolve({ items: [{ name: "Old" }], total: 1 });
  await t.tick();
  assert.match(t.element("#vm-video-catalog").innerHTML, /New/);
  assert.doesNotMatch(t.element("#vm-video-catalog").innerHTML, /Old/);
});

test("selecting a source resumes its durable scan and polls it", async () => {
  const t = setup();
  t.review.open();
  t.requests[0].resolve({
    items: [{ name: "Scene", cutscene_key: "CG", game_version: "1" }],
    total: 1,
  });
  await t.tick();
  const button = {
    dataset: { videoItem: "0" },
    setAttribute() {},
    removeAttribute() {},
  };
  t.element("#vm-video-catalog").events.click({
    target: { closest: () => button },
  });
  t.requests[1].resolve({
    sources: [
      {
        asset_node_id: 42,
        asset_version: "v1",
        game_version: "1",
        source_reference_id: 7,
        cutscene_key: "cutscene:M0390",
        source_sha256:
          "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        scan: { id: 8 },
      },
    ],
  });
  await t.tick();
  assert.match(t.requests[2].path, /scans\/8\?frame_offset=0&frame_limit=12$/);
  t.requests[2].resolve({
    id: 8,
    status: "running",
    source: {
      source_sha256:
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    },
    frames: [],
  });
  await t.tick();
  assert.match(t.element("#vm-video-status").textContent, /running/);
  assert.equal(t.timers.length, 1);
  t.timers[0]();
  assert.match(t.requests[3].path, /scans\/8\?frame_offset=0&frame_limit=12$/);
  t.requests[3].resolve({
    id: 8,
    status: "succeeded",
    unresolved_count: 0,
    frame_count: 0,
    next_frame_offset: null,
    source: {
      source_sha256:
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    },
    frames: [],
  });
  await t.tick();
  assert.equal(t.element("#vm-video-reviewed").disabled, false);
});

test("a late poll cannot replace a newly selected source", async () => {
  const t = setup();
  t.review.open();
  t.requests[0].resolve({
    items: [{ name: "Scene", cutscene_key: "CG", game_version: "1" }],
    total: 1,
  });
  await t.tick();
  const button = {
    dataset: { videoItem: "0" },
    setAttribute() {},
    removeAttribute() {},
  };
  t.element("#vm-video-catalog").events.click({
    target: { closest: () => button },
  });
  t.requests[1].resolve({
    game_version: "1",
    sources: [
      {
        asset_node_id: 42,
        asset_version: "a",
        source_reference_id: 7,
        cutscene_key: "cutscene:M0390",
        source_sha256:
          "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        scan: { id: 8 },
      },
      {
        asset_node_id: 43,
        asset_version: "b",
        source_reference_id: 9,
        cutscene_key: "cutscene:M0390",
        source_sha256:
          "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
      },
    ],
  });
  await t.tick();
  t.requests[2].resolve({
    id: 8,
    status: "running",
    source: {
      source_sha256:
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    },
    frames: [],
  });
  await t.tick();
  t.timers[0]();
  t.element("#vm-video-variant").value = "1";
  t.element("#vm-video-variant").events.change({
    currentTarget: t.element("#vm-video-variant"),
  });
  await t.tick();
  t.requests[3].resolve({
    id: 8,
    status: "succeeded",
    unresolved_count: 0,
    frame_count: 0,
    next_frame_offset: null,
    source: {
      source_sha256:
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    },
    reviewed: true,
    frames: [],
  });
  await t.tick();
  assert.match(t.element("#vm-video-source").textContent, /9/);
  assert.match(t.element("#vm-video-status").textContent, /No scan has been selected/);
  assert.equal(t.element("#vm-video-reviewed").disabled, true);
});

test("failed scans retry and reviewed plans can be opened without force", async () => {
  const t = setup();
  t.review.open();
  t.requests[0].resolve({
    items: [{ name: "Scene", cutscene_key: "CG" }],
    total: 1,
  });
  await t.tick();
  const button = {
    dataset: { videoItem: "0" },
    setAttribute() {},
    removeAttribute() {},
  };
  t.element("#vm-video-catalog").events.click({
    target: { closest: () => button },
  });
  t.requests[1].resolve({
    game_version: "3.7.0",
    sources: [
      {
        asset_node_id: 42,
        asset_version: "3.7.1",
        game_version: "3.7.0",
        source_reference_id: 7,
        cutscene_key: "cutscene:M0390",
        source_sha256:
          "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        scan: { id: 8 },
      },
    ],
  });
  await t.tick();
  t.requests[2].resolve({
    id: 8,
    status: "failed",
    source: {
      source_sha256:
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    },
    frames: [],
    error: "Worker stopped",
  });
  await t.tick();
  const form = t.element("#vm-video-scan");
  form.events.submit({ preventDefault() {}, currentTarget: form });
  await t.tick();
  assert.equal(t.requests[3].body.force, true);
  assert.equal(t.requests[3].body.asset_version, "3.7.1");
  assert.equal(t.requests[3].body.game_version, "3.7.0");
  assert.equal(t.requests[3].body.cutscene_key, "cutscene:M0390");
  assert.equal(t.requests[3].body.source_reference_id, 7);
  const preview = new URL(t.element("video").src, "http://test/");
  assert.equal(preview.searchParams.get("game_version"), "3.7.0");
  assert.equal(preview.searchParams.get("asset_version"), "3.7.1");
  assert.equal(preview.searchParams.get("cutscene_key"), "cutscene:M0390");
  assert.match(t.element("#vm-video-variant").innerHTML, /3\.7\.1/);
  assert.equal(
    t.requests[3].body.source_sha256,
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  );
  t.requests[3].resolve({ id: 9 });
  await t.tick();
  t.requests[4].resolve({
    id: 9,
    status: "succeeded",
    unresolved_count: 0,
    frame_count: 0,
    next_frame_offset: null,
    reviewed: true,
    sampling_mode: "frame",
    timestamp_seconds: 91.1,
    sampling_frame_offset: 0,
    source: {
      source_sha256:
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    },
    frames: [],
  });
  await t.tick();
  assert.equal(t.element("#vm-video-start").disabled, false);
  assert.match(t.element("#vm-video-status").textContent, /Reviewed scan/);
  const fields = form.elements;
  fields.sampling_mode.value = "frame"; fields.sampling_mode.change();
  fields.timestamp_seconds.value = "91.1";
  form.events.submit({ preventDefault() {}, currentTarget: form }); await t.tick();
  assert.equal(t.requests[5].body.force, false);
  assert.equal(t.requests[5].body.sampling_mode, "frame");
  t.requests[5].resolve({id:9}); await t.tick();
  t.requests[6].resolve({id:9,status:"succeeded",reviewed:true,sampling_mode:"frame",timestamp_seconds:91.1,sampling_frame_offset:0,source:{source_sha256:"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},frames:[],frame_count:0,unresolved_count:0,next_frame_offset:null}); await t.tick();
  fields.sampling_mode.value = "segment"; fields.sampling_mode.change();
  fields.start_seconds.value = "10"; fields.end_seconds.value = "20";
  form.events.submit({ preventDefault() {}, currentTarget: form }); await t.tick();
  assert.equal(t.requests[7].body.force, false);
  assert.equal(t.requests[7].body.sampling_mode, "segment");
  t.requests[7].resolve({id:10}); await t.tick();
  t.requests[8].resolve({id:10,status:"succeeded",reviewed:true,sampling_mode:"segment",start_seconds:10,end_seconds:20,source:{source_sha256:"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},frames:[],frame_count:0,unresolved_count:0,next_frame_offset:null}); await t.tick();
  fields.sampling_mode.value = "full"; fields.sampling_mode.change();
  form.events.submit({ preventDefault() {}, currentTarget: form }); await t.tick();
  assert.equal(t.requests[9].body.force, false);
  assert.equal(t.requests[9].body.sampling_mode, "full");
});

test("frame paging uses absolute indexes and scan-wide unresolved count", async () => {
  const t = setup();
  t.review.open();
  t.requests[0].resolve({
    items: [{ name: "Scene", cutscene_key: "CG" }],
    total: 1,
  });
  await t.tick();
  const select = {
    dataset: { videoItem: "0" },
    setAttribute() {},
    removeAttribute() {},
  };
  t.element("#vm-video-catalog").events.click({
    target: { closest: () => select },
  });
  t.requests[1].resolve({
    sources: [
      {
        asset_node_id: 1,
        asset_version: "1",
        game_version: "1",
        source_reference_id: 2,
        cutscene_key: "cutscene:M0390",
        source_sha256:
          "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        scan: { id: 8 },
      },
    ],
  });
  await t.tick();
  t.requests[2].resolve({
    id: 8,
    status: "succeeded",
    source: {
      source_sha256:
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    },
    frames: Array.from({ length: 12 }, (_, index) => ({
      index,
      time: index * 3,
      detections: [],
    })),
    frame_count: 121,
    unresolved_count: 1,
    next_frame_offset: 12,
  });
  await t.tick();
  assert.match(t.element("#vm-frame-page").textContent, /1–12 of 121/);
  assert.equal(t.element("#vm-video-reviewed").disabled, true);
  t.element("#vm-frame-next").events.click({
    currentTarget: t.element("#vm-frame-next"),
  });
  await t.tick();
  assert.match(t.requests[3].path, /frame_offset=12&frame_limit=12/);
  t.requests[3].resolve({
    id: 8,
    status: "succeeded",
    source: {
      source_sha256:
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    },
    frames: [
      {
        index: 12,
        time: 36,
        detections: [
          { index: 0, decision: "unresolved", links: [], candidates: [] },
        ],
      },
    ],
    frame_count: 121,
    unresolved_count: 1,
    next_frame_offset: 24,
  });
  await t.tick();
  assert.match(t.element("#vm-frame-page").textContent, /13–13 of 121/);
  const reject = {
    dataset: { frame: "12", detection: "0", decision: "reject" },
    setAttribute() {},
    removeAttribute() {},
  };
  t.element("#vm-video-frames").events.click({
    target: { closest: () => reject },
  });
  await t.tick();
  assert.match(t.requests[4].path, /observations\/12\/0\/resolve$/);
  t.requests[4].resolve({});
  await t.tick();
  assert.match(t.requests[5].path, /frame_offset=12/);
  t.requests[5].resolve({
    id: 8,
    status: "succeeded",
    source: {
      source_sha256:
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    },
    frames: [],
    frame_count: 121,
    unresolved_count: 0,
    next_frame_offset: 24,
  });
  await t.tick();
  assert.equal(t.element("#vm-video-reviewed").disabled, false);
  const form = t.element("#vm-video-scan");
  form.events.submit({preventDefault(){},currentTarget:form}); await t.tick();
  t.requests[6].resolve({id:9}); await t.tick();
  assert.match(t.requests[7].path, /scans\/9\?frame_offset=0&frame_limit=12/);
});

test("approval sends only supported identity link fields", async () => {
  const t = setup();
  t.review.open();
  t.requests[0].resolve({
    items: [{ name: "Scene", cutscene_key: "CG" }],
    total: 1,
  });
  await t.tick();
  const select = {
    dataset: { videoItem: "0" },
    setAttribute() {},
    removeAttribute() {},
  };
  t.element("#vm-video-catalog").events.click({
    target: { closest: () => select },
  });
  t.requests[1].resolve({
    sources: [
      {
        asset_node_id: 1,
        asset_version: "1",
        game_version: "1",
        source_reference_id: 2,
        cutscene_key: "cutscene:M0390",
        source_sha256:
          "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        scan: { id: 8 },
      },
    ],
  });
  await t.tick();
  t.requests[2].resolve({
    id: 8,
    status: "succeeded",
    source: {
      source_sha256:
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    },
    frames: [
      {
        index: 0,
        time: 0,
        detections: [
          {
            index: 0,
            decision: "pending",
            links: [
              {
                identity_id: 4,
                appearance_id: 9,
                name: "Hsin",
                kind: "character",
                appearance_name: "Outfit",
              },
            ],
            candidates: [],
          },
        ],
      },
    ],
    frame_count: 1,
    unresolved_count: 0,
    next_frame_offset: null,
  });
  await t.tick();
  const approve = {
    dataset: { frame: "0", detection: "0", decision: "approve" },
    setAttribute() {},
    removeAttribute() {},
  };
  t.element("#vm-video-frames").events.click({
    target: { closest: () => approve },
  });
  await t.tick();
  assert.deepEqual(JSON.parse(JSON.stringify(t.requests[3].body)), {
    decision: "approve",
    links: [{ identity_id: 4, appearance_id: 9 }],
  });
});

test("a scan from a different source hash is never shown as reviewed", async () => {
  const t = setup();
  t.review.open();
  t.requests[0].resolve({
    items: [{ name: "Scene", cutscene_key: "CG" }],
    total: 1,
  });
  await t.tick();
  const select = {
    dataset: { videoItem: "0" },
    setAttribute() {},
    removeAttribute() {},
  };
  t.element("#vm-video-catalog").events.click({
    target: { closest: () => select },
  });
  t.requests[1].resolve({
    sources: [
      {
        asset_node_id: 1,
        asset_version: "1",
        game_version: "1",
        source_reference_id: 2,
        cutscene_key: "cutscene:M0390",
        source_sha256:
          "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        scan: { id: 8 },
      },
    ],
  });
  await t.tick();
  t.requests[2].resolve({
    id: 8,
    status: "succeeded",
    reviewed: true,
    source: {
      source_sha256:
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    },
    frames: [],
    frame_count: 0,
    unresolved_count: 0,
    next_frame_offset: null,
  });
  await t.tick();
  assert.match(t.element("#vm-video-status").textContent, /source has changed/);
  assert.equal(t.element("#vm-video-reviewed").disabled, true);
  assert.equal(t.element("#vm-video-start").disabled, false);
});

test("requires complete cutscene context before preview or scanning", async () => {
  const t = setup();
  t.review.open();
  t.requests[0].resolve({
    items: [{ name: "M0390", cutscene_key: "M0390", game_version: "3.7.0" }],
    total: 1,
  });
  await t.tick();
  const select = {
    dataset: { videoItem: "0" },
    setAttribute() {},
    removeAttribute() {},
  };
  t.element("#vm-video-catalog").events.click({
    target: { closest: () => select },
  });
  assert.match(t.requests[1].path, /cutscene_key=M0390/);
  t.requests[1].resolve({
    sources: [
      {
        asset_node_id: 42,
        asset_version: "3.7.1",
        game_version: "3.7.0",
        source_reference_id: 7,
        source_sha256:
          "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      },
    ],
  });
  await t.tick();
  assert.match(
    t.element("#vm-video-status").textContent,
    /missing its cutscene or version context/,
  );
  assert.equal(t.element("video").src, undefined);
  assert.equal(t.element("#vm-video-start").disabled, true);
});

test("frame editor shares box gestures and persists absolute indexes with locked crops", async () => {
  const t = setup();
  t.review.open();
  t.requests[0].resolve({
    items: [{ name: "Scene", cutscene_key: "CG" }],
    total: 1,
  });
  await t.tick();
  t.element("#vm-video-catalog").events.click({
    target: {
      closest: () => ({
        dataset: { videoItem: "0" },
        setAttribute() {},
        removeAttribute() {},
      }),
    },
  });
  t.requests[1].resolve({
    sources: [
      {
        asset_node_id: 1,
        asset_version: "1",
        game_version: "1",
        source_reference_id: 2,
        cutscene_key: "CG",
        source_sha256: "abc",
        scan: { id: 8 },
      },
    ],
  });
  await t.tick();
  const frame = {
    index: 12,
    time: 36,
    image_url: "/frame",
    width: 100,
    height: 100,
    detections: [
      { index: 3, bbox: [1, 2, 20, 30], decision: "unresolved" },
      { index: 4, bbox: [30, 30, 50, 50], decision: "unresolved" },
      {
        index: 5,
        bbox: [60, 60, 80, 80],
        decision: "approved",
        reference_id: 99,
      },
    ],
  };
  t.requests[2].resolve({
    id: 8,
    status: "succeeded",
    source: { source_sha256: "abc" },
    frames: [frame],
    frame_count: 13,
    unresolved_count: 2,
    next_frame_offset: null,
  });
  await t.tick();
  assert.match(t.element("#vm-video-frames").innerHTML, /data-edit-frame="12"/);
  t.element("#vm-video-frames").events.click({
    target: { closest: () => ({ dataset: { editFrame: "12" } }) },
  });
  assert.match(t.html, /Detector boxes are proposals/);
  const overlay = t.body.querySelector("#vm-frame-edit-overlay"),
    list = t.body.querySelector("#vm-frame-edit-boxes");
  assert.match(list.innerHTML, /Object 3 · read-only/);
  assert.doesNotMatch(overlay.innerHTML, /data-resize="2"/);
  const handle = {
    dataset: { resize: "0" },
    closest: (s) => (s === "[data-resize]" ? handle : null),
  };
  overlay.events.pointerdown({
    target: handle,
    currentTarget: overlay,
    pointerId: 1,
  });
  overlay.events.pointerup({ clientX: 25, clientY: 35 });
  const remove = { dataset: { removeBox: "1" }, closest: () => remove };
  list.events.click({ target: remove });
  overlay.events.pointerdown({
    target: { closest: () => null },
    currentTarget: overlay,
    pointerId: 2,
    clientX: 10,
    clientY: 40,
  });
  overlay.events.pointerup({ clientX: 25, clientY: 55 });
  const saving = t.save();
  await t.tick();
  t.busy = true;
  const coordinate = {
    dataset: { boxCoordinate: "2", boxIndex: "0" },
    value: "95",
    closest() {
      return this;
    },
  };
  list.events.change({ target: coordinate });
  assert.equal(coordinate.value, 25);
  const blockedRemove = {
    dataset: { removeBox: "0" },
    closest() {
      return this;
    },
  };
  list.events.click({ target: blockedRemove });
  overlay.events.pointerdown({
    target: { closest: () => null },
    currentTarget: overlay,
    pointerId: 3,
    clientX: 1,
    clientY: 1,
  });
  overlay.events.pointerup({ clientX: 99, clientY: 99 });
  assert.match(t.requests[3].path, /observations\/12\/4\/resolve$/);
  assert.deepEqual(JSON.parse(JSON.stringify(t.requests[3].body)), {
    decision: "reject",
    links: [],
  });
  t.requests[3].resolve({});
  await t.tick();
  assert.match(t.requests[4].path, /observations\/12\/3\/bbox$/);
  assert.equal(t.requests[4].method, "PATCH");
  assert.deepEqual(JSON.parse(JSON.stringify(t.requests[4].body)), {
    bbox: [1, 2, 25, 35],
  });
  t.requests[4].resolve({});
  await t.tick();
  assert.match(t.requests[5].path, /frames\/12\/observations$/);
  assert.deepEqual(JSON.parse(JSON.stringify(t.requests[5].body)), {
    bbox: [10, 40, 25, 55],
  });
  t.requests[5].resolve({});
  await t.tick();
  assert.match(t.requests[6].path, /frame_offset=0&frame_limit=12/);
  t.requests[6].resolve({
    id: 8,
    status: "succeeded",
    source: { source_sha256: "abc" },
    frames: [frame],
    frame_count: 13,
    unresolved_count: 2,
    next_frame_offset: null,
  });
  await saving;
  t.busy = false;
});

test("partial frame save reloads before reopening and does not repeat a successful add", async () => {
  const t = setup();
  t.review.open();
  t.requests[0].resolve({
    items: [{ name: "Scene", cutscene_key: "CG" }],
    total: 1,
  });
  await t.tick();
  t.element("#vm-video-catalog").events.click({
    target: {
      closest: () => ({
        dataset: { videoItem: "0" },
        setAttribute() {},
        removeAttribute() {},
      }),
    },
  });
  t.requests[1].resolve({
    sources: [
      {
        asset_node_id: 1,
        asset_version: "1",
        game_version: "1",
        source_reference_id: 2,
        cutscene_key: "CG",
        source_sha256: "abc",
        scan: { id: 8 },
      },
    ],
  });
  await t.tick();
  const frame = {
    index: 12,
    time: 36,
    image_url: "/frame",
    width: 100,
    height: 100,
    detections: [],
  };
  const scan = {
    id: 8,
    status: "succeeded",
    source: { source_sha256: "abc" },
    frames: [frame],
    frame_count: 13,
    unresolved_count: 0,
    next_frame_offset: null,
  };
  t.requests[2].resolve(scan);
  await t.tick();
  const edit = () =>
    t
      .element("#vm-video-frames")
      .events.click({
        target: { closest: () => ({ dataset: { editFrame: "12" } }) },
      });
  edit();
  const overlay = t.body.querySelector("#vm-frame-edit-overlay");
  const draw = (x, y) => {
    overlay.events.pointerdown({
      target: { closest: () => null },
      currentTarget: overlay,
      pointerId: 1,
      clientX: x,
      clientY: y,
    });
    overlay.events.pointerup({ clientX: x + 10, clientY: y + 10 });
  };
  draw(10, 10);
  draw(30, 30);
  const saving = t.save();
  const failed = assert.rejects(saving, /Reopen the frame/);
  await t.tick();
  t.requests[3].resolve({});
  await t.tick();
  t.requests[4].reject(new Error("network error"));
  await t.tick();
  assert.match(t.requests[5].path, /frame_offset=0/);
  t.requests[5].resolve({
    ...scan,
    frames: [
      {
        ...frame,
        detections: [
          { index: 0, bbox: [10, 10, 20, 20], decision: "unresolved" },
        ],
      },
    ],
  });
  await failed;
  assert.equal(t.closed, true);
  edit();
  assert.match(
    t.body.querySelector("#vm-frame-edit-boxes").innerHTML,
    /Object 1/,
  );
  const retry = t.save();
  await t.tick();
  assert.match(t.requests[6].path, /scans\/8\?frame_offset=0/);
  t.requests[6].resolve(scan);
  await retry;
  assert.equal(
    t.requests.filter((r) => r.path.endsWith("/frames/12/observations")).length,
    2,
  );
});

async function samplingSetup() {
  const t = setup();
  t.review.open();
  t.requests[0].resolve({
    items: [{ name: "Scene", cutscene_key: "CG" }],
    total: 1,
  });
  await t.tick();
  t.element("#vm-video-catalog").events.click({
    target: {
      closest: () => ({
        dataset: { videoItem: "0" },
        setAttribute() {},
        removeAttribute() {},
      }),
    },
  });
  t.requests[1].resolve({
    sources: [
      {
        asset_node_id: 1,
        asset_version: "1",
        game_version: "1",
        source_reference_id: 2,
        cutscene_key: "CG",
        source_sha256: "abc",
      },
    ],
  });
  await t.tick();
  return t;
}

test("switching from a failed plan never forces another plan", async () => {
  const t = await samplingSetup(), form = t.element("#vm-video-scan"), fields = form.elements;
  form.events.submit({preventDefault(){},currentTarget:form}); await t.tick();
  t.requests[2].resolve({id:8}); await t.tick();
  t.requests[3].resolve({id:8,status:"failed",sampling_mode:"full",interval_seconds:3,source:{source_sha256:"abc"},frames:[],frame_count:0,unresolved_count:0,next_frame_offset:null}); await t.tick();
  fields.sampling_mode.value="segment"; fields.sampling_mode.change();
  fields.start_seconds.value="10"; fields.end_seconds.value="20";
  form.events.submit({preventDefault(){},currentTarget:form}); await t.tick();
  assert.equal(t.requests[4].body.force,false);
  assert.equal(t.requests[4].body.sampling_mode,"segment");
});

test("full video sampling stays the default at three seconds", async () => {
  const t = await samplingSetup(),
    form = t.element("#vm-video-scan");
  form.events.submit({ preventDefault() {}, currentTarget: form });
  await t.tick();
  const body = t.requests[2].body;
  assert.equal(body.sampling_mode, "full");
  assert.equal(body.interval_seconds, 3);
  for (const field of [
    "start_seconds",
    "end_seconds",
    "timestamp_seconds",
    "frame_offset",
  ])
    assert.equal(field in body, false);
});

test("segment sampling converts minute timestamps and validates its range", async () => {
  const t = await samplingSetup(),
    form = t.element("#vm-video-scan"),
    fields = form.elements;
  fields.sampling_mode.value = "segment";
  fields.sampling_mode.change();
  assert.equal(t.element("#vm-sampling-start").hidden, false);
  assert.equal(t.element("#vm-sampling-timestamp").hidden, true);
  fields.start_seconds.value = "1:31.10";
  fields.end_seconds.value = "90";
  form.events.submit({ preventDefault() {}, currentTarget: form });
  await t.tick();
  assert.equal(t.requests.length, 2);
  assert.match(t.messages.at(-1).text, /End time must be after/);
  fields.end_seconds.value = "1:60";
  form.events.submit({ preventDefault() {}, currentTarget: form });
  await t.tick();
  assert.equal(t.requests.length, 2);
  assert.match(t.messages.at(-1).text, /valid non-negative time/);
  fields.end_seconds.value = "92.5";
  form.events.submit({ preventDefault() {}, currentTarget: form });
  await t.tick();
  const body = t.requests[2].body;
  assert.equal(body.sampling_mode, "segment");
  assert.equal(body.start_seconds, 91.1);
  assert.equal(body.end_seconds, 92.5);
  assert.equal(body.interval_seconds, 3);
  assert.equal("timestamp_seconds" in body, false);
  assert.equal("frame_offset" in body, false);
});

test("exact frame uses current player position and a separate frame offset", async () => {
  const t = await samplingSetup(),
    form = t.element("#vm-video-scan"),
    fields = form.elements;
  fields.sampling_mode.value = "frame";
  fields.sampling_mode.change();
  assert.equal(t.element("#vm-sampling-interval").hidden, true);
  assert.equal(fields.interval_seconds.disabled, true);
  assert.equal(t.element("#vm-sampling-timestamp").hidden, false);
  t.element("video").currentTime = 91.1;
  t.element("#vm-use-video-position").events.click();
  assert.equal(fields.timestamp_seconds.value, "91.1");
  fields.frame_offset.value = "-1";
  form.events.submit({ preventDefault() {}, currentTarget: form });
  await t.tick();
  assert.equal(t.requests.length, 2);
  assert.match(t.messages.at(-1).text, /non-negative whole number/);
  fields.frame_offset.value = "4";
  form.events.submit({ preventDefault() {}, currentTarget: form });
  await t.tick();
  const body = t.requests[2].body;
  assert.equal(body.sampling_mode, "frame");
  assert.equal(body.timestamp_seconds, 91.1);
  assert.equal(body.frame_offset, 4);
  for (const field of ["start_seconds", "end_seconds", "interval_seconds"])
    assert.equal(field in body, false);
  t.requests[2].resolve({ id: 8 });
  await t.tick();
  t.requests[3].resolve({
    id: 8,
    status: "succeeded",
    sampling_mode: "frame",
    timestamp_seconds: 91.1,
    sampling_frame_offset: 4,
    source: { source_sha256: "abc" },
    frames: [],
    frame_count: 0,
    unresolved_count: 0,
    next_frame_offset: null,
  });
  await t.tick();
  assert.match(
    t.element("#vm-video-status").textContent,
    /Frame at 91.1s · offset 4/,
  );
});
