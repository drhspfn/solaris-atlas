import { ArrowRight, Search, X } from 'lucide-react';
import React, { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';

export function SearchBox({ initial = '', category }: { initial?: string; category?: string }) {
  const [value, setValue] = useState(initial);
  const input = useRef<HTMLInputElement>(null);
  const [params] = useSearchParams();
  const navigate = useNavigate();
  useEffect(() => setValue(initial), [initial]);
  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const next = new URLSearchParams();
    if (value.trim()) next.set('q', value.trim());
    if (category && category !== 'all') next.set('category', category);
    for (const c of params.getAll('category'))
      if (!next.has('category')) next.append('category', c);
    navigate(`/search?${next}`);
  };
  return (
    <form className="search-box" onSubmit={submit}>
      <Search size={19} />
      <input
        ref={input}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="Search characters, events, factions, places..."
        aria-label="Search the archive"
      />
      {value && (
        <button
          type="button"
          className="search-clear"
          aria-label="Clear archive search"
          onClick={() => {
            setValue('');
            if (initial) {
              const next = new URLSearchParams(params);
              next.delete('q');
              navigate(`/search?${next}`);
            }
            input.current?.focus();
          }}
        >
          <X size={16} aria-hidden="true" />
        </button>
      )}
      <kbd>↵</kbd>
      <button aria-label="Search">
        <ArrowRight size={17} />
      </button>
    </form>
  );
}
