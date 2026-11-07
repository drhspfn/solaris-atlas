"use client";

import { Check, Copy } from "lucide-react";
import {
  forwardRef,
  useEffect,
  useRef,
  useState,
  type InputHTMLAttributes,
} from "react";
import "@/styles/ui-controls.css";

export type InputProps = InputHTMLAttributes<HTMLInputElement> & {
  copyable?: boolean;
  copyLabel?: string;
};
export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { className = "", copyable = false, copyLabel = "Copy", ...props },
  ref,
) {
  const [status, setStatus] = useState("");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );
  if (!copyable)
    return <input {...props} ref={ref} className={`ui-input ${className}`} />;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(
        String(props.value ?? props.defaultValue ?? ""),
      );
      setStatus("Copied");
    } catch {
      setStatus("Copy failed. Select the text and copy it manually.");
    }
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setStatus(""), 3000);
  };
  return (
    <div className="ui-copyable">
      <input
        {...props}
        readOnly
        ref={ref}
        className={`ui-input ${className}`}
      />
      <button
        type="button"
        className="ui-copyable-button"
        onClick={copy}
        aria-label={copyLabel}
        disabled={props.disabled}
      >
        {status === "Copied" ? <Check size={17} /> : <Copy size={17} />}
        <span>{status === "Copied" ? "Copied" : copyLabel}</span>
      </button>
      <span className="ui-copyable-status" role="status">
        {status}
      </span>
    </div>
  );
});
