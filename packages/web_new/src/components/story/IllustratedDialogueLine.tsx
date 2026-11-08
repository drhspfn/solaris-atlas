"use client";

import type { CSSProperties } from "react";
import {
  gameTextPlainText,
  gameTextRuns,
  type PlayerDisplay,
} from "@/data/gameText";
import { localizedText } from "@/data/localized";
import { PlayerText } from "@/components/dialogue/PlayerText";
import { GAME_DIALOGUE_UI } from "@/data/dialogueUiAssets";

type SceneLine = {
  speaker?: { label?: unknown } | null;
  text?: { content?: string | null; inline_text?: string | null } | null;
};

/** Per-character opacity preserves text geometry and the game's inline formatting. */
export function IllustratedDialogueLine({
  line,
  display,
  duration,
  animate,
  hideSeparator = false,
  showContinue = true,
}: {
  line: SceneLine;
  display: PlayerDisplay;
  duration: number;
  animate: boolean;
  hideSeparator?: boolean;
  showContinue?: boolean;
}) {
  const source = localizedText(line.text, "Text unavailable in this locale");
  const runs = gameTextRuns(source, display.gender);
  const parts = runs.flatMap((run) =>
    run.text.split(/(\{PlayerName\})/gi).map((part) => ({
      ...run,
      isPlayer: /^\{PlayerName\}$/i.test(part),
      text: /^\{PlayerName\}$/i.test(part) ? display.name : part,
      color:
        /^\{PlayerName\}$/i.test(part) && display.colorMode === "custom"
          ? display.customColor
          : run.color,
    })),
  );
  const count = parts.reduce(
    (total, part) => total + Array.from(part.text).length,
    0,
  );
  let offset = 0;
  return (
    <div className="illustrated-dialogue-line">
      <div className="illustrated-dialogue-speaker">
        <PlayerText
          display={display}
          value={line.speaker?.label}
          fallback="Narration / Unknown speaker"
        />
      </div>
      {!hideSeparator && (
        <div className="illustrated-dialogue-divider" aria-hidden="true">
          <span />
          <img src={GAME_DIALOGUE_UI.separatorOrnament} alt="" />
          <span />
        </div>
      )}
      <p
        className="illustrated-dialogue-text"
        aria-label={gameTextPlainText(
          line.text,
          display,
          "Text unavailable in this locale",
        )}
      >
        <span aria-hidden="true">
          {parts.map((part, partIndex) => {
            const characters = Array.from(part.text);
            const start = offset;
            offset += characters.length;
            const style: CSSProperties = {
              color: part.color,
              fontStyle: part.italic ? "italic" : undefined,
              fontWeight: part.bold ? 700 : undefined,
              textDecoration:
                [
                  part.underline ? "underline" : "",
                  part.strike ? "line-through" : "",
                ]
                  .filter(Boolean)
                  .join(" ") || undefined,
            };
            return (
              <span
                key={partIndex}
                style={style}
                className={part.isPlayer ? "narrative-player-name" : undefined}
              >
                {characters.map((char, index) => (
                  <span
                    key={index}
                    className={
                      animate ? "illustrated-dialogue-character" : undefined
                    }
                    style={
                      animate
                        ? {
                            animationDelay: `${((start + index) * duration) / Math.max(count, 1)}ms`,
                            animationDuration: `${duration / Math.max(count, 1)}ms`,
                          }
                        : undefined
                    }
                  >
                    {char}
                  </span>
                ))}
              </span>
            );
          })}
        </span>
      </p>
      <span
        className="illustrated-dialogue-continue"
        aria-hidden="true"
        data-visible={showContinue}
      >
        <img
          className="illustrated-dialogue-continue-main"
          src={GAME_DIALOGUE_UI.continue}
          alt=""
        />
        <img
          className="illustrated-dialogue-continue-accent"
          src={GAME_DIALOGUE_UI.continueTriangle}
          alt=""
          hidden
        />
      </span>
    </div>
  );
}
