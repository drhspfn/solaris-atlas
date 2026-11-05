"use client";

import type { ReactNode } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "./Button";
import "@/styles/pagination.css";

type CommonProps = {
  disabled?: boolean;
  className?: string;
  label?: ReactNode;
  ariaLabel?: string;
};
export type PaginationProps = CommonProps &
  (
    | {
        mode: "pages";
        page: number;
        pageSize: number;
        total: number;
        onPageChange: (page: number) => void;
      }
    | {
        mode: "cursor";
        hasPrevious: boolean;
        hasNext: boolean;
        onPrevious: () => void;
        onNext: () => void;
      }
  );

export function Pagination(props: PaginationProps) {
  const { disabled = false, className = "", ariaLabel = "Pagination" } = props;
  const total =
    props.mode === "pages" && Number.isFinite(props.total)
      ? Math.max(0, Math.floor(props.total))
      : 0;
  const size =
    props.mode === "pages" && Number.isFinite(props.pageSize)
      ? Math.max(1, Math.floor(props.pageSize))
      : 1;
  const pageCount = Math.ceil(total / size);
  const page =
    props.mode === "pages" && Number.isFinite(props.page)
      ? Math.max(1, Math.min(pageCount || 1, Math.floor(props.page)))
      : 1;
  const canPrevious =
    !disabled &&
    (props.mode === "pages" ? total > 0 && page > 1 : props.hasPrevious);
  const canNext =
    !disabled && (props.mode === "pages" ? page < pageCount : props.hasNext);
  const summary =
    props.label ??
    (props.mode === "pages"
      ? total
        ? `Page ${page} of ${pageCount} · ${(page - 1) * size + 1}–${Math.min(page * size, total)} of ${total}`
        : "0 results"
      : null);

  return (
    <nav className={`ui-pagination ${className}`} aria-label={ariaLabel}>
      <Button
        variant="secondary"
        size="sm"
        icon={<ChevronLeft />}
        disabled={!canPrevious}
        onClick={() => {
          if (!canPrevious) return;
          if (props.mode === "pages") props.onPageChange(page - 1);
          else props.onPrevious();
        }}
      >
        Previous
      </Button>
      <span
        className="ui-pagination-summary"
        aria-live="polite"
        aria-atomic="true"
      >
        {summary}
      </span>
      <Button
        variant="secondary"
        size="sm"
        icon={<ChevronRight />}
        iconPosition="right"
        disabled={!canNext}
        onClick={() => {
          if (!canNext) return;
          if (props.mode === "pages") props.onPageChange(page + 1);
          else props.onNext();
        }}
      >
        Next
      </Button>
    </nav>
  );
}
