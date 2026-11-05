"use client";

import {
  cloneElement,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";
import {
  tooltipPosition,
  type TooltipAlign,
  type TooltipSide,
} from "./tooltipPosition";
import "@/styles/tooltip.css";
import { CircleHelp } from "lucide-react";

type Trigger = { "aria-describedby"?: string };
export type TooltipProps = {
  children: ReactElement<Trigger>;
  content: ReactNode;
  side?: TooltipSide;
  align?: TooltipAlign;
  delay?: number;
  offset?: number;
  disabled?: boolean;
  className?: string;
};

/** Native top-layer popup remains visible inside dialogs without portals or z-index tricks. */
export function Tooltip({
  children,
  content,
  side = "top",
  align = "center",
  delay = 250,
  offset = 8,
  disabled = false,
  className = "",
}: TooltipProps) {
  const id = useId();
  const anchor = useRef<HTMLSpanElement>(null);
  const popup = useRef<HTMLSpanElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [open, setOpen] = useState(false);
  function cancel() {
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = null;
  }
  function close() {
    cancel();
    setOpen(false);
  }
  function show() {
    cancel();
    if (!disabled)
      timer.current = setTimeout(() => setOpen(true), Math.max(0, delay));
  }
  useEffect(() => () => cancel(), []);
  useEffect(() => {
    if (disabled) close();
  }, [disabled]);
  useEffect(() => {
    if (!open || disabled || !popup.current || !anchor.current) return;
    const element = popup.current;
    element.showPopover();
    function position() {
      if (!anchor.current) return;
      const point = tooltipPosition(
        anchor.current.getBoundingClientRect(),
        element.getBoundingClientRect(),
        { width: window.innerWidth, height: window.innerHeight },
        side,
        align,
        offset,
      );
      Object.assign(element.style, {
        left: `${point.left}px`,
        top: `${point.top}px`,
      });
    }
    function escape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.stopPropagation();
        close();
      }
    }
    position();
    window.addEventListener("resize", position);
    window.addEventListener("scroll", position, true);
    document.addEventListener("keydown", escape, true);
    return () => {
      element.hidePopover();
      window.removeEventListener("resize", position);
      window.removeEventListener("scroll", position, true);
      document.removeEventListener("keydown", escape, true);
    };
  }, [open, disabled, side, align, offset, content]);
  const describedBy =
    [children.props["aria-describedby"], open && !disabled ? id : null]
      .filter(Boolean)
      .join(" ") || undefined;
  return (
    <span
      ref={anchor}
      className={`ui-tooltip ${className}`}
      onPointerEnter={show}
      onPointerLeave={() => {
        cancel();
        timer.current = setTimeout(() => setOpen(false), 100);
      }}
      onFocusCapture={show}
      onBlurCapture={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) close();
      }}
    >
      {cloneElement(children, { "aria-describedby": describedBy })}
      <span
        ref={popup}
        id={id}
        role="tooltip"
        popover="manual"
        hidden={!open || disabled}
        className="ui-tooltip-content"
        onPointerEnter={cancel}
        onPointerLeave={close}
      >
        {content}
      </span>
    </span>
  );
}

/** Visible field text plus a focusable explanation, including for disabled controls. */
export function HelpLabel({
  label,
  content,
}: {
  label: string;
  content: ReactNode;
}) {
  return (
    <Tooltip content={content} side="top" align="end" delay={600}>
      <span className="ui-help-label" role="group" aria-label={label}>
        {label}
        <button
          type="button"
          className="ui-help-trigger"
          aria-label={`About ${label}`}
        >
          <CircleHelp size={13} aria-hidden="true" />
        </button>
      </span>
    </Tooltip>
  );
}
