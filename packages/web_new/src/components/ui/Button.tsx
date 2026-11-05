"use client";

import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import "@/styles/ui-controls.css";

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  icon?: ReactNode;
  iconPosition?: "left" | "right";
  iconOnly?: boolean;
  loading?: boolean;
  loadingLabel?: string;
  variant?: "default" | "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md" | "lg" | "icon";
};

/** A native button with consistent targets and an explicit pending state. */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  function Button(
    {
      icon,
      iconPosition = "left",
      iconOnly = false,
      loading = false,
      loadingLabel,
      variant = "default",
      size = "md",
      className = "",
      children,
      disabled,
      type = "button",
      ...props
    },
    ref,
  ) {
    iconOnly = iconOnly || size === "icon";
    const graphic = loading ? (
      <span className="ui-button-spinner" aria-hidden="true" />
    ) : icon ? (
      <span className="ui-button-glyph" aria-hidden="true">
        {icon}
      </span>
    ) : null;
    return (
      <button
        {...props}
        ref={ref}
        type={type}
        disabled={disabled || loading}
        aria-busy={loading || undefined}
        className={`ui-button ui-button-${variant} ui-button-${size}${iconOnly ? " ui-button-icon-only" : ""} ${className}`}
      >
        {iconPosition === "left" && graphic}
        {!iconOnly && (
          <span className="ui-button-label">
            {loading && loadingLabel ? loadingLabel : children}
          </span>
        )}
        {iconPosition === "right" && graphic}
      </button>
    );
  },
);
