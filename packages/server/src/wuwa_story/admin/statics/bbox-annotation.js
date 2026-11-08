(() => {
  "use strict";
  const valid = (box, width, height) =>
    box.length === 4 &&
    box.every(Number.isFinite) &&
    box[0] >= 0 &&
    box[1] >= 0 &&
    box[2] <= width &&
    box[3] <= height &&
    box[0] < box[2] &&
    box[1] < box[3];
  function bounds(boxes, width, height) {
    return boxes
      .map(
        (b, i) =>
          `<button type="button" class="vm-bound${b.readonly ? " vm-bound-readonly" : ""}" data-index="${i}" aria-label="Object ${i + 1}${b.readonly ? ", read only" : ""}" style="left:${(100 * b.bbox[0]) / width}%;top:${(100 * b.bbox[1]) / height}%;width:${(100 * (b.bbox[2] - b.bbox[0])) / width}%;height:${(100 * (b.bbox[3] - b.bbox[1])) / height}%"><span>${i + 1}</span>${b.readonly ? "" : `<span class="vm-resize" data-resize="${i}" aria-hidden="true"></span>`}</button>`,
      )
      .join("");
  }
  function bind({
    overlay,
    image,
    dimensions,
    boxes,
    disabled,
    onAdd,
    onResize,
    render,
  }) {
    let dragStart = null,
      resizing = null,
      draft = null;
    function clearDraft() {
      draft?.remove();
      draft = null;
    }
    function point(e) {
      const { width, height } = dimensions(),
        rect = image.getBoundingClientRect();
      return [
        Math.round(
          Math.max(
            0,
            Math.min(width, ((e.clientX - rect.left) * width) / rect.width),
          ),
        ),
        Math.round(
          Math.max(
            0,
            Math.min(height, ((e.clientY - rect.top) * height) / rect.height),
          ),
        ),
      ];
    }
    overlay.addEventListener("pointerdown", (e) => {
      if (disabled()) return;
      const handle = e.target.closest("[data-resize]");
      if (handle) {
        const index = +handle.dataset.resize,
          box = boxes()[index];
        if (!box || box.readonly) return;
        resizing = { index, original: box.bbox.slice() };
        e.currentTarget.setPointerCapture(e.pointerId);
        return;
      }
      if (e.target.closest("button")) return;
      dragStart = point(e);
      e.currentTarget.setPointerCapture(e.pointerId);
    });
    overlay.addEventListener("pointermove", (e) => {
      if (disabled()) {
        clearDraft();
        return;
      }
      if (dragStart) {
        const end = point(e),
          { width, height } = dimensions();
        if (!draft) {
          draft = overlay.ownerDocument.createElement("div");
          draft.className = "vm-bound vm-bound-draft";
          draft.setAttribute("aria-hidden", "true");
          overlay.appendChild(draft);
        }
        draft.style.left = (100 * Math.min(dragStart[0], end[0])) / width + "%";
        draft.style.top = (100 * Math.min(dragStart[1], end[1])) / height + "%";
        draft.style.width =
          (100 * Math.abs(end[0] - dragStart[0])) / width + "%";
        draft.style.height =
          (100 * Math.abs(end[1] - dragStart[1])) / height + "%";
        return;
      }
      if (!resizing) return;
      const end = point(e),
        box = resizing.original,
        bound = overlay.querySelector(
          `.vm-bound[data-index="${resizing.index}"]`,
        ),
        { width, height } = dimensions();
      if (bound && end[0] > box[0] && end[1] > box[1]) {
        bound.style.width = (100 * (end[0] - box[0])) / width + "%";
        bound.style.height = (100 * (end[1] - box[1])) / height + "%";
      }
    });
    overlay.addEventListener("pointerup", (e) => {
      clearDraft();
      if (disabled()) {
        dragStart = null;
        resizing = null;
        return;
      }
      const { width, height } = dimensions();
      if (resizing) {
        const end = point(e),
          original = resizing.original,
          next = [original[0], original[1], end[0], end[1]],
          index = resizing.index;
        resizing = null;
        if (
          valid(next, width, height) &&
          (next[2] !== original[2] || next[3] !== original[3])
        )
          onResize(index, next);
        render();
        return;
      }
      if (!dragStart) return;
      const end = point(e),
        box = [
          Math.min(dragStart[0], end[0]),
          Math.min(dragStart[1], end[1]),
          Math.max(dragStart[0], end[0]),
          Math.max(dragStart[1], end[1]),
        ];
      dragStart = null;
      if (valid(box, width, height)) {
        onAdd(box);
        render();
      }
    });
    overlay.addEventListener("pointercancel", () => {
      clearDraft();
      dragStart = null;
      resizing = null;
      if (!disabled()) render();
    });
  }
  globalThis.visualMemoryBbox = { valid, bounds, bind };
})();
