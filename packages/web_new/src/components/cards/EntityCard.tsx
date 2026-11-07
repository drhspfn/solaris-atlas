import { ArrowRight } from 'lucide-react';
import { Link } from '@/lib/router';

import { apiUrl } from '../../api/client';
import { APP_SETTINGS } from '../../config/settings';
import { display as entityDisplay, type Entity, entityPath } from '../../data/entities';
import { PlayerText, type PlayerDisplay } from '../dialogue/PlayerText';
import { Skeleton } from '../ui/Skeleton';

export function EntityCard({ item, index = 0, display }: { item: Entity; index?: number; display?: PlayerDisplay }) {
  const type = item.category ?? (item.node_type === 'area' ? 'location' : item.node_type);
  const imageUrl = item.character_kind === 'npc' ? null : item.image_url;
  return (
    <Link
      to={entityPath(item)}
      className="entity-card"
      style={{
        animationDelay: `${Math.min(index, APP_SETTINGS.presentation.cardStaggerMaxIndex) * APP_SETTINGS.presentation.cardStaggerMs}ms`,
      }}
    >
      <div className={`entity-art art-${type}${imageUrl ? ' has-image' : ''}`}>
        {imageUrl ? (
          <img
            className="entity-card-image"
            src={apiUrl(imageUrl.replace(/^\/api/, ''))}
            alt=""
            loading="lazy"
          />
        ) : (
          <span className="art-glyph">
            {type === 'character' ? '✳' : type === 'quest' ? '◈' : type === 'item' ? '✧' : '⌖'}
          </span>
        )}
        <span className="art-type">{type}</span>
        <span className="art-id">{item.canonical_key}</span>
      </div>
      <div className="entity-info">
        <div className="entity-title-row">
          <h3><PlayerText value={entityDisplay(item)} display={display} /></h3>
          <ArrowRight className="arrow-diagonal" />
        </div>
        <p>
          {item.node_type === 'quest'
            ? `Quest ID ${item.canonical_key.split(':').at(-1)}`
            : item.node_type === 'character'
              ? item.character_kind === 'npc' ? 'NPC' : item.character_kind === 'story' ? 'Story & event role' : item.character_kind === 'technical' ? 'Technical role' : item.character_kind === 'playable' ? 'Playable resonator' : 'Character'
              : type === 'location'
                ? 'Area · Location'
                : item.node_type}
        </p>
      </div>
    </Link>
  );
}

export function EntityCardSkeleton() {
  return <div className="entity-card">
    <div className="entity-art has-image"><Skeleton style={{ height: '100%' }} /></div>
    <div className="entity-info">
      <div className="entity-title-row"><Skeleton style={{ width: '70%', height: 15 }} /></div>
      <p><Skeleton style={{ width: '45%', height: 10 }} /></p>
    </div>
  </div>;
}
