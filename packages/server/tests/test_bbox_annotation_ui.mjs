import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

test("drawing shows a draft until release or cancellation", () => {
  const context = {};
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
  const events = {},
    added = [],
    children = [];
  const overlay = {
    ownerDocument: {
      createElement: () => ({
        style: {},
        setAttribute() {},
        remove() {
          children.splice(children.indexOf(this), 1);
        },
      }),
    },
    addEventListener: (name, fn) => (events[name] = fn),
    appendChild: (element) => children.push(element),
    setPointerCapture() {},
  };
  context.visualMemoryBbox.bind({
    overlay,
    image: {
      getBoundingClientRect: () => ({
        left: 0,
        top: 0,
        width: 100,
        height: 100,
      }),
    },
    dimensions: () => ({ width: 200, height: 100 }),
    boxes: () => [],
    disabled: () => false,
    onAdd: (box) => added.push(box),
    onResize() {},
    render() {},
  });
  const start = () =>
    events.pointerdown({
      target: { closest: () => null },
      currentTarget: overlay,
      pointerId: 1,
      clientX: 70,
      clientY: 60,
    });
  start();
  events.pointermove({ clientX: 20, clientY: 10 });
  assert.equal(children.length, 1);
  assert.equal(children[0].className, "vm-bound vm-bound-draft");
  assert.deepEqual(children[0].style, {
    left: "20%",
    top: "10%",
    width: "50%",
    height: "50%",
  });
  assert.equal(added.length, 0);
  events.pointercancel();
  assert.equal(children.length, 0);
  assert.equal(added.length, 0);
  start();
  events.pointermove({ clientX: 20, clientY: 10 });
  events.pointerup({ clientX: 20, clientY: 10 });
  assert.equal(children.length, 0);
  assert.deepEqual(Array.from(added[0]), [40, 10, 140, 60]);
});
