import { memo, useState, type CSSProperties, type HTMLAttributes } from "react";

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
  if (!term || (!term.title && !term.description)) {
    return (
      <span className="game-term-plain" style={style}>
        {label}
      </span>
    );
  }
  return (
    <span
      className="game-term-wrap"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        className="game-term-trigger"
        style={style}
        aria-expanded={open}
        onClick={() => setOpen((prev) => !prev)}
      >
        {label}
      </button>
      {open && (
        <span className="game-term-popover" role="tooltip">
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
      )}
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
