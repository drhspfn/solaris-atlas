import { memo, useRef, useState, type CSSProperties, type HTMLAttributes } from "react";
import { createPortal } from "react-dom";

import { gameTextRuns, type PlayerDisplay } from "../../data/gameText";
import { localizedText } from "../../data/localized";

export type { PlayerDisplay } from "../../data/gameText";
export type GameTerm = {
  id?: number;
  title?: string | null;
  description?: string | null;
  color?: string | null;
};
export type PlayerTextProps = Omit<
  HTMLAttributes<HTMLSpanElement>,
  "children" | "dangerouslySetInnerHTML"
> & {
  value: unknown;
  fallback?: string;
  display?: PlayerDisplay;
  terms?: Record<string, GameTerm>;
};

function TermRun({
  label,
  term,
  style,
  display,
  terms,
}: {
  label: string;
  term?: GameTerm;
  style: CSSProperties;
  display?: PlayerDisplay;
  terms?: Record<string, GameTerm>;
}) {
  const [open, setOpen] = useState(false);
  const [coords, setCoords] = useState<{ top: number; left: number } | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  if (!term || (!term.title && !term.description)) {
    return (
      <span className="game-term-plain" style={style}>
        {label}
      </span>
    );
  }

  const show = () => {
    const rect = triggerRef.current?.getBoundingClientRect();
    if (rect && typeof window !== "undefined") {
      const width = Math.min(360, window.innerWidth - 24);
      const left = Math.max(12, Math.min(rect.left, window.innerWidth - width - 12));
      const top = Math.min(rect.bottom + 6, window.innerHeight - 16);
      setCoords({ top, left });
    }
    setOpen(true);
  };

  const popover = open && (
    <span
      className="game-term-popover"
      role="tooltip"
      style={coords ? { top: coords.top, left: coords.left } : undefined}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      {term.title && <span className="game-term-title">{term.title}</span>}
      {term.description && (
        <span className="game-term-desc">
          <PlayerText
            value={term.description}
            display={display}
            terms={terms}
          />
        </span>
      )}
    </span>
  );

  return (
    <span
      className="game-term-wrap"
      onMouseEnter={show}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        ref={triggerRef}
        type="button"
        className="game-term-trigger"
        style={style}
        aria-expanded={open}
        onClick={() => (open ? setOpen(false) : show())}
      >
        {label}
      </button>
      {popover && (typeof document !== "undefined" ? createPortal(popover, document.body) : popover)}
    </span>
  );
}

/** Hook-free atomic game text. The owning page supplies one memoized display. */
export const PlayerText = memo(function PlayerText({
  value,
  fallback = "",
  display,
  terms,
  style,
  ...props
}: PlayerTextProps) {
  const runs = gameTextRuns(localizedText(value, fallback), display?.gender);
  return (
    <span {...props} style={{ whiteSpace: "pre-line", ...style }}>
      {runs.map((run, index) => {
        const runStyle: CSSProperties = {
          color: run.color,
          fontStyle: run.italic ? "italic" : undefined,
          fontWeight: run.bold ? 700 : undefined,
          textDecoration:
            [run.underline ? "underline" : "", run.strike ? "line-through" : ""]
              .filter(Boolean)
              .join(" ") || undefined,
        };
        if (run.termId) {
          return (
            <TermRun
              key={index}
              label={run.text}
              term={terms?.[run.termId]}
              style={runStyle}
              display={display}
              terms={terms}
            />
          );
        }
        return (
          <span key={index} style={runStyle}>
            {run.text.split(/(\{PlayerName\})/gi).map((part, partIndex) =>
              /^\{PlayerName\}$/i.test(part) ? (
                <span
                  className="narrative-player-name"
                  key={partIndex}
                  style={{
                    color:
                      display?.colorMode === "custom"
                        ? display.customColor
                        : run.color,
                  }}
                >
                  {display?.name || "Rover"}
                </span>
              ) : (
                part
              ),
            )}
          </span>
        );
      })}
    </span>
  );
});
