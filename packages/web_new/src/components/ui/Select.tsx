'use client';

import { Check, ChevronDown } from 'lucide-react';
import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';

export type SelectOption = {
  value: string | number;
  label: string;
  group?: string;
  disabled?: boolean;
};

/** Single-value selector. Native popovers provide the top layer, Escape and outside dismissal. */
export function Select({
  value,
  onChange,
  options,
  label,
  className = '',
  disabled = false,
  placeholder = 'Select',
}: {
  value: string | number;
  onChange: (value: string) => void;
  options: readonly SelectOption[];
  label: string;
  className?: string;
  disabled?: boolean;
  placeholder?: string;
}) {
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const search = useRef({ text: '', time: 0 });
  const selected = options.findIndex((option) => String(option.value) === String(value));

  function close(restoreFocus = true) {
    panel.current?.hidePopover();
    setOpen(false);
    if (restoreFocus) trigger.current?.focus({ preventScroll: true });
  }
  function show() {
    setActive(Math.max(0, selected));
    panel.current?.showPopover();
    setOpen(true);
  }
  function choose(index: number) {
    const option = options[index];
    if (!option || option.disabled) return;
    onChange(String(option.value));
    close();
  }
  useEffect(() => {
    if (!open) return;
    function position() {
      const button = trigger.current,
        popup = panel.current;
      if (!button || !popup) return;
      const rect = button.getBoundingClientRect();
      if (rect.bottom < 0 || rect.top > window.innerHeight) {
        popup.hidePopover();
        return;
      }
      const below = window.innerHeight - rect.bottom - 12;
      const above = below < 220 && rect.top > below;
      const width = Math.min(Math.max(rect.width, 240), window.innerWidth - 24);
      Object.assign(popup.style, {
        width: `${width}px`,
        left: `${Math.max(12, Math.min(rect.left, window.innerWidth - width - 12))}px`,
        top: `${above ? rect.top - 6 : rect.bottom + 6}px`,
        maxHeight: `${Math.max(44, Math.min(340, above ? rect.top - 18 : below))}px`,
        transform: above ? 'translateY(-100%)' : 'none',
      });
    }
    position();
    panel.current?.focus({ preventScroll: true });
    window.addEventListener('resize', position);
    window.addEventListener('scroll', position, true);
    return () => {
      window.removeEventListener('resize', position);
      window.removeEventListener('scroll', position, true);
    };
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const option = panel.current?.querySelector<HTMLElement>(`[data-index="${active}"]`);
    if (option && panel.current) {
      const popup = panel.current;
      if (option.offsetTop < popup.scrollTop) popup.scrollTop = option.offsetTop;
      else if (option.offsetTop + option.offsetHeight > popup.scrollTop + popup.clientHeight)
        popup.scrollTop = option.offsetTop + option.offsetHeight - popup.clientHeight;
    }
  }, [active, open]);

  function keyDown(event: KeyboardEvent) {
    if (event.nativeEvent.isComposing) return;
    if (event.key === 'Tab') {
      close(false);
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      close();
      return;
    }
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      choose(active);
      return;
    }
    let next = active;
    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = options.length - 1;
      else next = (active + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length;
      const step = event.key === 'ArrowUp' || event.key === 'End' ? -1 : 1;
      for (let count = 0; count < options.length && options[next]?.disabled; count++)
        next = (next + step + options.length) % options.length;
      setActive(next);
    } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      const now = Date.now();
      const text = (now - search.current.time < 700 ? search.current.text : '') + event.key;
      search.current = { text, time: now };
      const match = options.findIndex(
        (option) =>
          !option.disabled && option.label.toLocaleLowerCase().startsWith(text.toLocaleLowerCase()),
      );
      if (match >= 0) setActive(match);
    }
  }

  return (
    <span className={`atlas-select ${className}`}>
      <button
        ref={trigger}
        type="button"
        className="atlas-select-trigger"
        disabled={disabled || !options.length}
        aria-label={`${label}: ${options[selected]?.label ?? placeholder}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => (open ? close() : show())}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            show();
          }
        }}
      >
        <span>{options[selected]?.label ?? placeholder}</span>
        <ChevronDown size={15} aria-hidden="true" />
      </button>
      <div
        ref={panel}
        id={id}
        className="atlas-select-popup"
        popover="auto"
        role="listbox"
        tabIndex={-1}
        aria-label={label}
        aria-activedescendant={options[active] ? `${id}-${active}` : undefined}
        onToggle={(event) => setOpen(event.newState === 'open')}
        onKeyDown={keyDown}
      >
        {options.map((option, index) => (
          <div key={option.value}>
            {option.group && option.group !== options[index - 1]?.group && (
              <div className="atlas-select-group">{option.group}</div>
            )}
            <div
              id={`${id}-${index}`}
              role="option"
              data-index={index}
              aria-selected={index === selected}
              aria-disabled={option.disabled || undefined}
              className={`atlas-select-option${index === active ? ' is-focused' : ''}`}
              onPointerMove={() => setActive(index)}
              onClick={() => choose(index)}
            >
              <span>{option.label}</span>
              {index === selected && <Check size={16} aria-hidden="true" />}
            </div>
          </div>
        ))}
      </div>
    </span>
  );
}
