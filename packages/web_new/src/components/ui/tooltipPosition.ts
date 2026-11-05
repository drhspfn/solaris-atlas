export type TooltipSide = "top" | "right" | "bottom" | "left";
export type TooltipAlign = "start" | "center" | "end";
type Rect = {
  left: number;
  top: number;
  right: number;
  bottom: number;
  width: number;
  height: number;
};
export function tooltipPosition(
  anchor: Rect,
  popup: Pick<Rect, "width" | "height">,
  viewport: { width: number; height: number },
  side: TooltipSide,
  align: TooltipAlign,
  offset: number,
) {
  const edge = 8;
  const space = {
    top: anchor.top,
    bottom: viewport.height - anchor.bottom,
    left: anchor.left,
    right: viewport.width - anchor.right,
  };
  const opposite = {
    top: "bottom",
    bottom: "top",
    left: "right",
    right: "left",
  } as const;
  const required =
    (side === "top" || side === "bottom" ? popup.height : popup.width) +
    offset +
    edge;
  if (space[side] < required && space[opposite[side]] > space[side])
    side = opposite[side];
  const vertical = side === "top" || side === "bottom";
  const cross = vertical
    ? anchor.width - popup.width
    : anchor.height - popup.height;
  const shift = align === "start" ? 0 : align === "end" ? cross : cross / 2;
  const left = vertical
    ? anchor.left + shift
    : side === "left"
      ? anchor.left - popup.width - offset
      : anchor.right + offset;
  const top = vertical
    ? side === "top"
      ? anchor.top - popup.height - offset
      : anchor.bottom + offset
    : anchor.top + shift;
  return {
    left: Math.max(edge, Math.min(left, viewport.width - popup.width - edge)),
    top: Math.max(edge, Math.min(top, viewport.height - popup.height - edge)),
  };
}
