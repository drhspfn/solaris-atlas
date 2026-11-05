'use client';

import { ArrowLeft, Check, ChevronDown, ChevronRight, Search, X } from 'lucide-react';
import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';

export type SelectOption = {
  value: string | number;
  label: string;
  group?: string;
  path?: readonly string[];
  disabled?: boolean;
};

export type SelectCategory = {
  label: string;
  options?: readonly SelectOption[];
  children?: readonly SelectCategory[];
};

/** Nested catalogs and flat path-bearing options share the same selection identity. */
export function flattenSelectCategories(categories: readonly SelectCategory[], parent: readonly string[] = []): SelectOption[] {
  return categories.flatMap(category => {
    const path = [...parent, category.label];
    return [...(category.options || []).map(option => ({ ...option, path: [...path, ...(option.path || [])] })),
      ...flattenSelectCategories(category.children || [], path)];
  });
}

/** Single-value selector. Native popovers provide the top layer, Escape and outside dismissal. */
export function Select({
  value,
  onChange,
  options: sourceOptions = [],
  categories = [],
  searchable = false,
  categorized = false,
  rememberPosition = false,
  label,
  className = '',
  disabled = false,
  placeholder = 'Select',
}: {
  value: string | number;
  onChange: (value: string) => void;
  options?: readonly SelectOption[];
  categories?: readonly SelectCategory[];
  searchable?: boolean;
  categorized?: boolean;
  rememberPosition?: boolean;
  label: string;
  className?: string;
  disabled?: boolean;
  placeholder?: string;
}) {
  const id = useId();
  const rich = searchable || categorized || categories.length > 0;
  const options = categories.length ? [...sourceOptions, ...flattenSelectCategories(categories)] : sourceOptions;
  const [path, setPath] = useState<readonly string[]>([]);
  const [query, setQuery] = useState('');
  const scrollPositions = useRef(new Map<string, number>());
  const restoreScroll = useRef(false);
  const searchInput = useRef<HTMLInputElement>(null);
  const previousValue = useRef(value);
  const pathOf = (option: SelectOption) => option.path || (categorized && option.group ? [option.group] : []);
  const matching = options.filter(option => query
    ? [...pathOf(option), option.label].join(' ').toLocaleLowerCase().includes(query.toLocaleLowerCase())
    : !categorized || path.every((part, index) => pathOf(option)[index] === part));
  const branches = categorized && !query
    ? [...new Set(matching.flatMap(option => pathOf(option)[path.length] ? [pathOf(option)[path.length]] : []))] : [];
  const rows: (SelectOption & { branch?: string })[] = rich ? [
    ...branches.map(branch => ({ value: `category:${branch}`, label: branch, branch })),
    ...matching.filter(option => query || !categorized || pathOf(option).length <= path.length),
  ] : [...options];
  const scrollKey = JSON.stringify([path, query]);
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const search = useRef({ text: '', time: 0 });
  const selected = options.findIndex((option) => String(option.value) === String(value));

  function rememberScroll() {
    if (rememberPosition && panel.current) scrollPositions.current.set(scrollKey, panel.current.scrollTop);
  }
  function navigate(next: readonly string[]) {
    rememberScroll(); restoreScroll.current = true; setPath(next); setActive(0);
  }
  function close(restoreFocus = true) {
    rememberScroll();
    panel.current?.hidePopover();
    setOpen(false);
    if (restoreFocus) trigger.current?.focus({ preventScroll: true });
  }
  function show() {
    if (rich) {
      if (!rememberPosition) { setPath(selected >= 0 ? pathOf(options[selected]) : []); setQuery(''); }
      restoreScroll.current = rememberPosition;
      setActive(Math.max(0, rows.findIndex(option => !option.branch && String(option.value) === String(value))));
    } else setActive(Math.max(0, selected));
    panel.current?.showPopover();
    setOpen(true);
  }
  function choose(index: number) {
    const option = rows[index];
    if (!option || option.disabled) return;
    if (option.branch) { navigate([...path, option.branch]); return; }
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
      const width = Math.min(Math.max(rect.width, rich ? 340 : 240), window.innerWidth - 24);
      Object.assign(popup.style, {
        width: `${width}px`,
        left: `${Math.max(12, Math.min(rect.left, window.innerWidth - width - 12))}px`,
        top: `${above ? rect.top - 6 : rect.bottom + 6}px`,
        maxHeight: `${Math.max(44, Math.min(rich ? 440 : 340, above ? rect.top - 18 : below))}px`,
        transform: above ? 'translateY(-100%)' : 'none',
      });
    }
    position();
    if (searchable) searchInput.current?.focus({ preventScroll: true });
    else panel.current?.focus({ preventScroll: true });
    window.addEventListener('resize', position);
    window.addEventListener('scroll', position, true);
    return () => {
      window.removeEventListener('resize', position);
      window.removeEventListener('scroll', position, true);
    };
  }, [open]);
  useEffect(() => {
    if (!rich || previousValue.current === value) return;
    previousValue.current = value;
    if (selected >= 0) {
      const nextPath = pathOf(options[selected]);
      const siblings = options.filter(option => nextPath.every((part, index) => pathOf(option)[index] === part));
      const nestedBranches = new Set(siblings.flatMap(option => pathOf(option)[nextPath.length] ? [pathOf(option)[nextPath.length]] : []));
      const leaves = siblings.filter(option => pathOf(option).length <= nextPath.length);
      setPath(nextPath); setQuery('');
      setActive(categorized ? nestedBranches.size + leaves.findIndex(option => String(option.value) === String(value)) : selected);
      restoreScroll.current = false;
    }
  }, [value, selected]);
  useEffect(() => {
    if (!open) return;
    if (rich && restoreScroll.current && panel.current) {
      panel.current.scrollTop = scrollPositions.current.get(scrollKey) || 0;
      restoreScroll.current = false;
      return;
    }
    const option = panel.current?.querySelector<HTMLElement>(`[data-index="${active}"]`);
    if (option && panel.current) {
      const popup = panel.current;
      const header = popup.querySelector<HTMLElement>('.atlas-select-catalog-heading')?.offsetHeight || 0;
      if (option.offsetTop < popup.scrollTop + header) popup.scrollTop = Math.max(0, option.offsetTop - header);
      else if (option.offsetTop + option.offsetHeight > popup.scrollTop + popup.clientHeight)
        popup.scrollTop = option.offsetTop + option.offsetHeight - popup.clientHeight;
    }
  }, [active, open, scrollKey]);

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
    const typing = (event.target as HTMLElement).tagName === 'INPUT';
    if ((event.target as HTMLElement).tagName === 'BUTTON' && ['Enter', ' '].includes(event.key)) return;
    if (event.key === 'ArrowLeft' && !typing && path.length) { event.preventDefault(); navigate(path.slice(0, -1)); return; }
    if (event.key === 'Enter' || (event.key === ' ' && !typing)) {
      event.preventDefault();
      choose(active);
      return;
    }
    let next = active;
    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = rows.length - 1;
      else next = (active + (event.key === 'ArrowDown' ? 1 : -1) + rows.length) % rows.length;
      const step = event.key === 'ArrowUp' || event.key === 'End' ? -1 : 1;
      for (let count = 0; count < rows.length && rows[next]?.disabled; count++)
        next = (next + step + rows.length) % rows.length;
      setActive(next);
    } else if (!typing && event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      const now = Date.now();
      const text = (now - search.current.time < 700 ? search.current.text : '') + event.key;
      search.current = { text, time: now };
      const match = rows.findIndex(
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
        className={`atlas-select-popup${rich ? ' atlas-select-catalog' : ''}`}
        popover="auto"
        role={rich ? "dialog" : "listbox"}
        tabIndex={-1}
        aria-label={label}
        aria-activedescendant={rows[active] ? `${id}-${active}` : undefined}
        onToggle={(event) => { if (event.newState !== 'open') rememberScroll(); setOpen(event.newState === 'open'); }}
        onKeyDown={keyDown}
      >
        {rich && <div className="atlas-select-catalog-heading">
          {searchable && <div className="atlas-select-search">
            <Search size={16} aria-hidden="true" />
            <input ref={searchInput} type="search" aria-label={`Search ${label.toLocaleLowerCase()}`}
              placeholder={`Search ${label.toLocaleLowerCase()}…`} value={query}
              onChange={event => { rememberScroll(); setQuery(event.target.value); setActive(0); restoreScroll.current = true; }} />
            {query && <button type="button" aria-label="Clear search" onClick={() => { setQuery(''); setActive(0); searchInput.current?.focus(); }}><X size={15} aria-hidden="true" /></button>}
          </div>}
          {categorized && <div className="atlas-select-breadcrumb">
            <button type="button" disabled={!path.length || !!query} onClick={() => navigate(path.slice(0, -1))}><ArrowLeft size={15} aria-hidden="true" /> Back</button>
            <span>{query ? 'Search all categories' : path.join(' / ') || 'All categories'}</span>
          </div>}
        </div>}
        <div role={rich ? "listbox" : undefined} aria-label={rich ? label : undefined}>
        {rows.map((option, index) => (
          <div key={option.value}>
            {!categorized && option.group && option.group !== rows[index - 1]?.group && (
              <div className="atlas-select-group">{option.group}</div>
            )}
            <div
              id={`${id}-${index}`}
              role="option"
              data-index={index}
              aria-selected={!option.branch && String(option.value) === String(value)}
              aria-disabled={option.disabled || undefined}
              className={`atlas-select-option${index === active ? ' is-focused' : ''}`}
              onPointerMove={() => setActive(index)}
              onClick={() => choose(index)}
            >
              <span>{option.label}</span>
              {option.branch ? <ChevronRight size={16} aria-hidden="true" />
                : String(option.value) === String(value) && <Check size={16} aria-hidden="true" />}
            </div>
          </div>
        ))}
        {rich && !rows.length && <p className="atlas-select-empty" role="status">No matching options.</p>}
        </div>
      </div>
    </span>
  );
}
