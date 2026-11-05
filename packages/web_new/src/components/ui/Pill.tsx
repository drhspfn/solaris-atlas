"use client";

import type {
  AnchorHTMLAttributes,
  ButtonHTMLAttributes,
  HTMLAttributes,
} from "react";
import { Link } from "@/lib/router";
import "@/styles/pill.css";

type InfoPill = HTMLAttributes<HTMLSpanElement> & { kind?: "info" };
type ButtonPill = ButtonHTMLAttributes<HTMLButtonElement> & {
  kind: "button";
  selected?: boolean;
};
type LinkPill = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href"> & {
  kind: "link";
  href: string;
  selected?: boolean;
};
export type PillProps = InfoPill | ButtonPill | LinkPill;

export function Pill(props: PillProps) {
  if (props.kind === "button") {
    const {
      kind,
      selected = false,
      className = "",
      type = "button",
      ...rest
    } = props;
    return (
      <button
        {...rest}
        type={type}
        aria-pressed={selected}
        className={`ui-pill ui-pill-interactive ${className}`}
      />
    );
  }
  if (props.kind === "link") {
    const { kind, selected = false, className = "", href, ...rest } = props;
    return (
      <Link
        {...rest}
        href={href}
        aria-current={selected ? "page" : undefined}
        className={`ui-pill ui-pill-interactive ${className}`}
      />
    );
  }
  const { kind, className = "", ...rest } = props;
  return <span {...rest} className={`ui-pill ${className}`} />;
}
