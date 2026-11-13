import React from "react";
import "@/styles/index.css";

export type PageHeaderProps = {
  title: React.ReactNode;
  description?: React.ReactNode;
  showPeriod?: boolean;
  actions?: React.ReactNode;
  className?: string;
  align?: "start" | "center" | "end";
  as?: "header" | "div";
  children?: React.ReactNode;
};

export function PageHeader({
  title,
  description,
  showPeriod = true,
  actions,
  className = "",
  align,
  as: Component = "header",
  children,
}: PageHeaderProps) {
  const fullClassName = `page-heading ${className}`.trim();
  const alignStyle = align
    ? {
        alignItems:
          align === "start"
            ? "flex-start"
            : align === "end"
              ? "flex-end"
              : "center",
      }
    : undefined;

  return (
    <Component className={fullClassName} style={alignStyle}>
      <div className="page-heading-main">
        <h1>
          {title}
          {showPeriod && <span className="heading-period">.</span>}
        </h1>
        {description &&
          (typeof description === "string" ? (
            <p>{description}</p>
          ) : (
            description
          ))}
        {children}
      </div>
      {actions && <div className="page-heading-actions">{actions}</div>}
    </Component>
  );
}
