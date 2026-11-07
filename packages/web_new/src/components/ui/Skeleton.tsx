import type { CSSProperties, ReactNode } from "react";

/** Decorative geometry; the owning card defines its silhouette. */
export function Skeleton({
  className = "",
  style,
}: {
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <span
      className={`ui-skeleton ${className}`}
      style={style}
      aria-hidden="true"
    />
  );
}

/** One accessible pending status for a set of decorative placeholders. */
export function SkeletonGroup({
  label,
  className = "",
  children,
}: {
  label: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div
      className={`ui-skeleton-group ${className}`}
      role="status"
      aria-busy="true"
    >
      <span className="ui-skeleton-label">{label}</span>
      {children}
    </div>
  );
}

export function TextSkeleton({
  lines = 3,
  className = "",
}: {
  lines?: number;
  className?: string;
}) {
  return (
    <div className={`ui-skeleton-lines ${className}`} aria-hidden="true">
      {Array.from({ length: lines }, (_, index) => (
        <Skeleton
          key={index}
          style={{
            width: index === lines - 1 ? "68%" : index % 2 ? "88%" : "100%",
          }}
        />
      ))}
    </div>
  );
}
