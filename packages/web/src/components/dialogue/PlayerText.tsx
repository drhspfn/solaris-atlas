import { memo } from 'react';

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
  const text = localizedText(value, fallback);
  const parts = text.split(/(\{PlayerName\})/gi);
  return (
    <>
      {parts.map((part, index) =>
        /^\{PlayerName\}$/i.test(part) ? (
          <span
            className="narrative-player-name"
            style={display.colorMode === 'custom' ? { color: display.customColor } : undefined}
            key={index}
          >
            {display.name}
          </span>
        ) : (
          part
        ),
      )}
    </>
  );
});
