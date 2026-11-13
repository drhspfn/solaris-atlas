"use client";

import { ArrowRight, Search, X } from "lucide-react";
import React, { forwardRef, useImperativeHandle, useRef } from "react";
import { Button } from "@/components/ui/Button";
import "@/styles/ui-controls.css";
import "@/styles/catalog-controls.css";

export type SearchInputProps = {
  value: string;
  onChange: (value: string) => void;
  onSubmit?: (value: string) => void;
  onClear?: () => void;
  onCompositionStart?: React.CompositionEventHandler<HTMLInputElement>;
  onCompositionEnd?: React.CompositionEventHandler<HTMLInputElement>;
  onKeyDown?: React.KeyboardEventHandler<HTMLInputElement>;
  placeholder?: string;
  "aria-label"?: string;
  ariaLabel?: string;
  className?: string;
  loading?: boolean;
  maxLength?: number;
  disabled?: boolean;
  autoFocus?: boolean;
  showSubmitButton?: boolean;
  id?: string;
  name?: string;
  asForm?: boolean;
};

export const SearchInput = forwardRef<HTMLInputElement, SearchInputProps>(
  function SearchInput(
    {
      value,
      onChange,
      onSubmit,
      onClear,
      onCompositionStart,
      onCompositionEnd,
      onKeyDown,
      placeholder = "Search...",
      ariaLabel,
      className = "",
      loading = false,
      maxLength = 256,
      disabled = false,
      autoFocus = false,
      showSubmitButton = true,
      id,
      name,
      asForm = false,
      ...props
    },
    ref,
  ) {
    const innerRef = useRef<HTMLInputElement>(null);
    const isComposingRef = useRef(false);
    useImperativeHandle(ref, () => innerRef.current as HTMLInputElement);

    const handleSubmit = (e?: React.FormEvent) => {
      e?.preventDefault();
      if (!isComposingRef.current) {
        onSubmit?.(value);
      }
    };

    const handleClear = () => {
      onChange("");
      onClear?.();
      innerRef.current?.focus();
    };

    const content = (
      <>
        <span className="ui-search-bar-icon" aria-hidden="true">
          <Search size={17} />
        </span>
        <input
          ref={innerRef}
          id={id}
          name={name}
          type="search"
          autoComplete="off"
          className="ui-input ui-search-bar-input"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          aria-label={ariaLabel || props["aria-label"] || placeholder}
          maxLength={maxLength}
          disabled={disabled}
          autoFocus={autoFocus}
          onCompositionStart={(e) => {
            isComposingRef.current = true;
            onCompositionStart?.(e);
          }}
          onCompositionEnd={(e) => {
            isComposingRef.current = false;
            onCompositionEnd?.(e);
          }}
          onKeyDown={(event) => {
            onKeyDown?.(event);
            if (event.defaultPrevented) return;
            if (
              event.key === "Enter" &&
              !event.nativeEvent.isComposing &&
              !isComposingRef.current
            ) {
              event.preventDefault();
              handleSubmit();
            }
          }}
        />
        {value && !disabled && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            icon={<X size={17} />}
            className="search-clear search-action"
            aria-label="Clear search"
            onClick={handleClear}
          />
        )}
        {showSubmitButton && (
          <Button
            type={asForm ? "submit" : "button"}
            variant="ghost"
            size="icon"
            className="search-action"
            icon={<ArrowRight size={18} />}
            aria-label="Submit search"
            loading={loading}
            disabled={disabled}
            onClick={asForm ? undefined : () => handleSubmit()}
          />
        )}
      </>
    );

    const fullClassName = `ui-search-bar ui-search-form ${className}`.trim();

    if (asForm) {
      return (
        <form
          role="search"
          className={fullClassName}
          onSubmit={handleSubmit}
          noValidate
        >
          {content}
        </form>
      );
    }

    return (
      <div role="search" className={fullClassName}>
        {content}
      </div>
    );
  },
);
