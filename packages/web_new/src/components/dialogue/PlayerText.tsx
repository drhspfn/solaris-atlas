import { memo, type CSSProperties, type HTMLAttributes } from 'react';

import { gameTextRuns, type PlayerDisplay } from '../../data/gameText';
import { localizedText } from '../../data/localized';

export type { PlayerDisplay } from '../../data/gameText';
export type PlayerTextProps = Omit<HTMLAttributes<HTMLSpanElement>, 'children' | 'dangerouslySetInnerHTML'> & {
  value: unknown;
  fallback?: string;
  display?: PlayerDisplay;
};

/** Hook-free atomic game text. The owning page supplies one memoized display. */
export const PlayerText = memo(function PlayerText({ value, fallback = '', display, style, ...props }: PlayerTextProps) {
  const runs = gameTextRuns(localizedText(value, fallback), display?.gender);
  return <span {...props} style={{ whiteSpace: 'pre-line', ...style }}>
    {runs.map((run, index) => {
      const runStyle: CSSProperties = {
        color: run.color, fontStyle: run.italic ? 'italic' : undefined,
        fontWeight: run.bold ? 700 : undefined,
        textDecoration: [run.underline ? 'underline' : '', run.strike ? 'line-through' : ''].filter(Boolean).join(' ') || undefined,
      };
      return <span key={index} style={runStyle}>{run.text.split(/(\{PlayerName\})/gi).map((part, partIndex) =>
        /^\{PlayerName\}$/i.test(part) ? <span className="narrative-player-name" key={partIndex}
          style={{ color: display?.colorMode === 'custom' ? display.customColor : run.color }}>{display?.name || 'Rover'}</span> : part,
      )}</span>;
    })}
  </span>;
});
