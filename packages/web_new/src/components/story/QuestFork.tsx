import { GitFork, Merge } from 'lucide-react';
import type { ReactNode } from 'react';
import { PlayerText, type PlayerDisplay } from '../dialogue/PlayerText';
import type { QuestForkData } from './questReading';

export function QuestFork({
  fork,
  display,
  anchors,
  selected,
  renderPath,
}: {
  fork: QuestForkData;
  display: PlayerDisplay;
  anchors: Map<string, string>;
  selected: string | null;
  renderPath: (ids: string[]) => ReactNode;
}) {
  return (
    <section className="quest-fork" aria-label="Dialogue paths">
      <header className="quest-fork-heading">
        <GitFork size={18} />
        <span>Choose a conversation path</span>
        <small>Alternatives · not a sequence</small>
      </header>
      <div
        className="quest-fork-paths"
        role="region"
        tabIndex={0}
        aria-label="Alternative dialogue paths; scroll horizontally on small screens"
      >
        {fork.paths.map((path, index) => (
          <section
            key={path.id}
            className={`quest-fork-path${selected === path.id ? ' is-selected' : ''}`}
            aria-label={`Dialogue option ${index + 1}`}
          >
            <header>
              <span className="quest-path-number">{String(index + 1).padStart(2, '0')}</span>
              <h4>
                <PlayerText display={display} value={path.text} fallback="Dialogue option" />
              </h4>
            </header>
            <div className="quest-fork-content">{renderPath(path.lineIds)}</div>
            {!path.lineIds.length && <p>No separate response is recorded in this transcript.</p>}
            {path.continuation &&
              anchors.has(path.continuation) &&
              fork.continuation !== path.continuation && (
                <a href={`#${anchors.get(path.continuation)}`}>Continue this path ↗</a>
              )}
          </section>
        ))}
      </div>
      {fork.continuation && anchors.has(fork.continuation) && (
        <a className="quest-fork-join" href={`#${anchors.get(fork.continuation)}`}>
          <Merge size={16} />
          {fork.continuation === fork.source
            ? 'Return to this choice'
            : 'Paths rejoin · continue the story'}
        </a>
      )}
    </section>
  );
}
