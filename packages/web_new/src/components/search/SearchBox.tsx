"use client";

import { ArrowRight, Search, X } from "lucide-react";
import React, { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import "@/styles/catalog-controls.css";
import { useNavigate, useSearchParams } from "@/lib/router";

export function SearchBox({
  initial = "",
  category,
}: {
  initial?: string;
  category?: string;
}) {
  const [value, setValue] = useState(initial);
  const input = useRef<HTMLInputElement>(null);
  const params = useSearchParams();
  const navigate = useNavigate();
  useEffect(() => setValue(initial), [initial]);
  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const next = new URLSearchParams();
    if (value.trim()) next.set("q", value.trim());
    if (category && category !== "all") next.set("category", category);
    if (params) {
      for (const c of params.getAll("category")) {
        if (!next.has("category")) next.append("category", c);
      }
      for (const field of ["mode", "game_version", "locale"]) {
        const selected = params.get(field);
        if (selected) next.set(field, selected);
      }
    }
    navigate(`/search?${next.toString()}`);
  };
  return (
    <form className="search-box ui-search-form" noValidate onSubmit={submit}>
      <Search size={19} />
      <Input
        ref={input}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder={
          params?.get("mode") === "story"
            ? "Why did it happen? Who was involved?"
            : "Search characters, events, factions, places..."
        }
        aria-label={
          params?.get("mode") === "story"
            ? "Search story explanations"
            : "Search the archive"
        }
        maxLength={512}
        onKeyDown={(event) => {
          if (event.key === "Enter" && event.nativeEvent.isComposing)
            event.preventDefault();
        }}
      />
      {value && (
        <Button
          type="button"
          variant="ghost"
          size="icon"
          icon={<X size={17} />}
          className="search-clear search-action"
          aria-label="Clear archive search"
          onClick={() => {
            setValue("");
            if (initial && params) {
              const next = new URLSearchParams(params.toString());
              next.delete("q");
              navigate(`/search?${next.toString()}`);
            }
            input.current?.focus();
          }}
        />
      )}
      <kbd>↵</kbd>
      <Button
        type="submit"
        size="icon"
        variant="ghost"
        className="search-action"
        aria-label="Search"
        icon={<ArrowRight size={18} />}
      />
    </form>
  );
}
