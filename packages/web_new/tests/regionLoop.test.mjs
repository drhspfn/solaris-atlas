import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

function setup(fetchAudio = async () => ({ ok: true, arrayBuffer: async () => new ArrayBuffer(8) })) {
  const sources = [];
  class Context {
    currentTime = 0;
    destination = {};
    createGain() { return { gain: { value: 1 }, connect() {}, disconnect() {} }; }
    createBufferSource() {
      const source = { connect() {}, disconnect() {}, stop() { this.stopped = true; },
        start(_when, offset) { this.offset = offset; } };
      sources.push(source);
      return source;
    }
    async resume() {}
    async close() { this.closed = true; }
    async decodeAudioData() { return { duration: 10 }; }
  }
  let context;
  const exports = {};
  const source = readFileSync(new URL("../src/components/story/regionLoop.ts", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  vm.runInNewContext(code, { exports, AudioContext: class extends Context { constructor() { super(); context = this; } },
    fetch: fetchAudio, AbortController });
  return { ...exports, sources, get context() { return context; } };
}

test("sample boundaries loop and pause resumes at the wrapped position", async () => {
  const env = setup();
  const player = new env.RegionLoop("/music.ogg", { start_sample: 48000, end_sample: 192000, sample_rate: 48000 });
  await player.play();
  assert.equal(env.sources[0].loopStart, 1);
  assert.equal(env.sources[0].loopEnd, 4);
  assert.equal(env.sources[0].offset, 0);
  env.context.currentTime = 9;
  player.pause();
  assert.equal(env.sources[0].stopped, true);
  await player.play();
  assert.equal(env.sources[1].offset, 3);
  player.close();
  assert.equal(env.sources[1].stopped, true);
  assert.equal(env.context.closed, true);
});

test("pausing during download prevents delayed playback", async () => {
  let resolve;
  const env = setup(() => new Promise(done => { resolve = done; }));
  const player = new env.RegionLoop("/music.ogg", { start_sample: 1, end_sample: 2, sample_rate: 1 });
  const pending = player.play();
  player.pause();
  resolve({ ok: true, arrayBuffer: async () => new ArrayBuffer(8) });
  await pending;
  assert.equal(env.sources.length, 0);
  player.close();
});

test("rejects loop bounds beyond decoded audio and allows retry after a failed fetch", async () => {
  let attempts = 0;
  const env = setup(async () => ({ ok: ++attempts > 1, arrayBuffer: async () => new ArrayBuffer(8) }));
  const player = new env.RegionLoop("/music.ogg", { start_sample: 1, end_sample: 12, sample_rate: 1 });
  await assert.rejects(player.play(), /download failed/);
  assert.equal(player.paused, true);
  await assert.rejects(player.play(), /exceeds decoded audio/);
  assert.equal(env.sources.length, 0);
  player.close();
});
