import { ChevronRight } from "lucide-react";
import React from "react";
import Link from "next/link";
import "@/styles/index.css";

export type BreadcrumbItem = {
  label: React.ReactNode;
  to?: string;
  href?: string;
};

export type BreadcrumbsProps = {
  items?: BreadcrumbItem[];
  children?: React.ReactNode;
  className?: string;
};

export function Breadcrumbs({
  items,
  children,
  className = "",
}: BreadcrumbsProps) {
  const fullClassName = `breadcrumbs ${className}`.trim();

  if (children) {
    return (
      <nav aria-label="Breadcrumb" className={fullClassName}>
        {children}
      </nav>
    );
  }

  if (!items || items.length === 0) return null;

  return (
    <nav aria-label="Breadcrumb" className={fullClassName}>
      {items.map((item, index) => {
        const isLast = index === items.length - 1;
        const target = item.to || item.href;

        return (
          <React.Fragment key={index}>
            {index > 0 && <ChevronRight size={13} aria-hidden="true" />}
            {target && !isLast ? (
              <Link href={target}>{item.label}</Link>
            ) : (
              <span aria-current={isLast ? "page" : undefined}>
                {item.label}
              </span>
            )}
          </React.Fragment>
        );
      })}
    </nav>
  );
}
