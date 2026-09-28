import React, { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ArrowRight, Search } from "lucide-react";

export function SearchBox({
  initial = "",
  category,
}: {
  initial?: string;
  category?: string;
}) {
  const [value, setValue] = useState(initial);
  const [params] = useSearchParams();
  const navigate = useNavigate();
  useEffect(() => setValue(initial), [initial]);
  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const next = new URLSearchParams();
    if (value.trim()) next.set("q", value.trim());
    if (category && category !== "all") next.set("category", category);
    for (const c of params.getAll("category"))
      if (!next.has("category")) next.append("category", c);
    navigate(`/search?${next}`);
  };
  return (
    <form className="search-box" onSubmit={submit}>
      <Search size={19} />
      <input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="Search characters, events, factions, places..."
        aria-label="Search the archive"
      />
      <kbd>↵</kbd>
      <button aria-label="Search">
        <ArrowRight size={17} />
      </button>
    </form>
  );
}
