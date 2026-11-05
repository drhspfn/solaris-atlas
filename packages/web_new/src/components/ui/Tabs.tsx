"use client";

import {
  useId,
  useRef,
  type HTMLAttributes,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import "@/styles/ui-controls.css";

export type TabItem<T extends string = string> = {
  value: T;
  label: ReactNode;
  icon?: ReactNode;
  disabled?: boolean;
  /** Pair with the rendered tabpanel's id. */
  panelId?: string;
  /** Optional stable id for the tabpanel's aria-labelledby. */
  id?: string;
};
export type TabsProps<T extends string = string> = Omit<
  HTMLAttributes<HTMLDivElement>,
  "onChange"
> & {
  items: readonly TabItem<T>[];
  label?: string;
  value: T;
  onValueChange?: (value: T) => void;
  onChange?: (value: T) => void;
  variant?: "buttons" | "segmented" | "underline";
  orientation?: "horizontal" | "vertical";
};

/** Controlled tabs. Callers own panel content; arrow keys activate enabled tabs. */
export function Tabs<T extends string>({
  items,
  value,
  onValueChange,
  onChange,
  variant = "buttons",
  orientation = "horizontal",
  className = "",
  label,
  ...props
}: TabsProps<T>) {
  const generatedId = useId();
  const buttons = useRef<Record<string, HTMLButtonElement | null>>({});
  const enabled = items.filter((item) => !item.disabled);
  const focusValue = enabled.some((item) => item.value === value)
    ? value
    : enabled[0]?.value;
  const choose = (next: T) => (onValueChange ?? onChange)?.(next);
  function move(event: KeyboardEvent<HTMLButtonElement>, current: T) {
    if (event.defaultPrevented || !enabled.length) return;
    const forward = orientation === "horizontal" ? "ArrowRight" : "ArrowDown";
    const backward = orientation === "horizontal" ? "ArrowLeft" : "ArrowUp";
    const index = enabled.findIndex((item) => item.value === current);
    const nextIndex =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? enabled.length - 1
          : event.key === forward
            ? (index + 1) % enabled.length
            : event.key === backward
              ? (index - 1 + enabled.length) % enabled.length
              : -1;
    if (nextIndex < 0) return;
    event.preventDefault();
    const next = enabled[nextIndex].value;
    choose(next);
    buttons.current[next]?.focus();
  }
  return (
    <div
      {...props}
      role="tablist"
      aria-label={props["aria-label"] ?? label}
      aria-orientation={orientation}
      className={`ui-tabs ui-tabs-${variant} ${className}`}
    >
      {items.map((item, index) => (
        <button
          key={item.value}
          type="button"
          role="tab"
          id={item.id ?? `${generatedId}-tab-${index}`}
          aria-controls={item.panelId}
          aria-selected={value === item.value}
          disabled={item.disabled}
          tabIndex={focusValue === item.value ? 0 : -1}
          className="ui-tab"
          ref={(button) => {
            buttons.current[item.value] = button;
          }}
          onClick={() => choose(item.value)}
          onKeyDown={(event) => move(event, item.value)}
        >
          {item.icon && (
            <span className="ui-tab-icon" aria-hidden="true">
              {item.icon}
            </span>
          )}
          <span>{item.label}</span>
        </button>
      ))}
    </div>
  );
}
