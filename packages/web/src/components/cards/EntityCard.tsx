import { ArrowRight } from 'lucide-react';
import { Link } from 'react-router-dom';

import { APP_SETTINGS } from '../../config/settings';
import { display, type Entity, entityPath } from '../../data/entities';

export function EntityCard({ item, index = 0 }: { item: Entity; index?: number }) {
  const type = item.category ?? (item.node_type === 'area' ? 'location' : item.node_type);
  return (
    <Link
      to={entityPath(item)}
      className="entity-card"
      style={{
        animationDelay: `${Math.min(index, APP_SETTINGS.presentation.cardStaggerMaxIndex) * APP_SETTINGS.presentation.cardStaggerMs}ms`,
      }}
    >
      <div className={`entity-art art-${type}`}>
        <span className="art-glyph">
          {type === 'character' ? '✳' : type === 'quest' ? '◈' : type === 'item' ? '✧' : '⌖'}
        </span>
        <span className="art-type">{type}</span>
        <span className="art-id">{item.canonical_key}</span>
      </div>
      <div className="entity-info">
        <div className="entity-title-row">
          <h3>{display(item)}</h3>
          <ArrowRight className="arrow-diagonal" />
        </div>
        <p>
          {item.node_type === 'quest'
            ? `Quest ID ${item.canonical_key.split(':').at(-1)}`
            : item.node_type === 'character'
              ? 'Resonator · Character'
              : type === 'location'
                ? 'Area · Location'
                : item.node_type}
        </p>
      </div>
    </Link>
  );
}
