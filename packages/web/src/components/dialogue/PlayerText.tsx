import { Fragment, memo } from 'react';

import { gameTextRuns } from '../../data/gameText';
import { localizedText } from '../../data/localized';
import type { PlayerNameColorMode } from '../../preferences/NarrativePreferences';

export type PlayerDisplay = {
  name: string;
  colorMode: PlayerNameColorMode;
  customColor: string;
};

export const PlayerText = memo(function PlayerText({
  value,
  fallback = '',
  display,
}: {
  value: unknown;
  fallback?: string;
  display: PlayerDisplay;
}) {
  const runs = gameTextRuns(localizedText(value, fallback));
  return (
    <>
      {runs.map((run, index) => {
        const content = run.text.split(/(\{PlayerName\})/gi).map((part, partIndex) =>
          /^\{PlayerName\}$/i.test(part) ? (
            <span
              className="narrative-player-name"
              style={{ color: display.colorMode === 'custom' ? display.customColor : run.color }}
              key={partIndex}
            >
              {display.name}
            </span>
          ) : (
            part
          ),
        );
        return run.color ? (
          <span key={index} style={{ color: run.color }}>
            {content}
          </span>
        ) : (
          <Fragment key={index}>{content}</Fragment>
        );
      })}
    </>
  );
});
