import { ArrowRight, MapPin, Swords } from 'lucide-react';
import { Link } from 'react-router-dom';

import { localizedText } from '../../data/localized';
import { EmptyInline } from '../ui/Feedback';

type Text = { content?: string; key?: string } | null;
type Acquisition = {
  map_sources?: {
    map_id: number;
    url: string;
    names: Record<string, string>;
    map_names: Record<string, string>;
    kind: string;
    count: number;
    hidden_count: number;
    game_version: string;
  }[];
  acquisition_paths?: { id: number; description: Text }[];
  shop_offers?: {
    offer_id: number;
    shop_id: number;
    shop_name: Text;
    item_count: number;
    purchase_limit?: number;
    prices?: { amount: number; item_id: number; item?: { label: string } }[];
  }[];
  quest_rewards?: { quest: { game_quest_id: number; name: Text } }[];
  quest_rewards_has_more?: boolean;
  locations?: { node: { canonical_key: string; label: string } }[];
  harvest_world_maps?: { map_id: number; named_region?: { canonical_key?: string; name: Text } }[];
};

export function ItemAcquisition({ data, locale }: { data: Acquisition; locale: string }) {
  const maps = data.map_sources ?? [];
  const worlds = new Map<number, typeof maps>();
  for (const source of maps)
    worlds.set(source.map_id, [...(worlds.get(source.map_id) ?? []), source]);
  const shopNames = new Set(
    (data.shop_offers ?? []).map((shop) => localizedText(shop.shop_name)).filter(Boolean),
  );
  const paths = (data.acquisition_paths ?? []).filter(
    (path) => !shopNames.has(localizedText(path.description)),
  );
  const shops = data.shop_offers ?? [];
  const quests = data.quest_rewards ?? [];
  const regions = maps.length ? [] : (data.harvest_world_maps ?? []);
  const locations = data.locations ?? [];
  const name = (names: Record<string, string>) =>
    names[locale] || names.en || Object.values(names)[0];
  if (
    !maps.length &&
    !paths.length &&
    !shops.length &&
    !quests.length &&
    !regions.length &&
    !locations.length
  )
    return <EmptyInline text="No confirmed acquisition sources recorded yet." />;
  return (
    <div className="item-acquisition">
      {maps.length > 0 && (
        <div className="subsection">
          <h3>On the map</h3>
          {[...worlds.entries()].map(([worldId, sources]) => (
            <details key={worldId} className="acquisition-world" open={worlds.size === 1}>
              <summary>
                <strong>{name(sources[0].map_names) || 'World map'}</strong>
                <small>
                  {sources.reduce((sum, source) => sum + source.count, 0)} locations ·{' '}
                  {sources.length} sources
                </small>
              </summary>
              {sources.map((source) => (
                <Link
                  key={source.url}
                  to={source.url}
                  className="connection-row acquisition-map-link"
                >
                  {source.kind === 'loot_preview' ? <Swords size={20} /> : <MapPin size={20} />}
                  <div>
                    <strong>{name(source.names) || 'Collection points'}</strong>
                    <small>
                      {source.kind === 'loot_preview' ? 'Possible drops' : 'Gathering'} ·{' '}
                      {source.count} locations
                      {source.hidden_count > 0 ? ` · ${source.hidden_count} hidden` : ''}
                    </small>
                    <small>
                      {name(source.map_names) || 'World map'} · {source.game_version}
                    </small>
                  </div>
                  <span className="small-link">
                    Show on map <ArrowRight size={15} />
                  </span>
                </Link>
              ))}
            </details>
          ))}
        </div>
      )}
      {quests.length > 0 && (
        <div className="subsection">
          <h3>Quest rewards</h3>
          {quests.map(({ quest }) => (
            <Link
              key={quest.game_quest_id}
              to={`/quests/${quest.game_quest_id}`}
              className="connection-row"
            >
              <div>
                <strong>{localizedText(quest.name, `Quest ${quest.game_quest_id}`)}</strong>
                <small>Listed in possible quest rewards</small>
              </div>
              <ArrowRight size={16} />
            </Link>
          ))}
          {data.quest_rewards_has_more && (
            <small>Showing the first {quests.length} quest reward sources.</small>
          )}
        </div>
      )}
      {shops.length > 0 && (
        <div className="subsection">
          <h3>Shops</h3>
          {shops.map((shop) => (
            <div className="plain-row" key={shop.offer_id}>
              <span>
                <strong>{localizedText(shop.shop_name, `Shop ${shop.shop_id}`)}</strong>
                <small>
                  {shop.prices
                    ?.map(
                      (price) =>
                        `${price.amount} ${price.item?.label || `currency ${price.item_id}`}`,
                    )
                    .join(' + ')}{' '}
                  · limit {shop.purchase_limit ?? '—'}
                </small>
              </span>
              <small>{shop.item_count}× per purchase</small>
            </div>
          ))}
        </div>
      )}
      {(regions.length > 0 || locations.length > 0) && (
        <div className="subsection">
          <h3>Recorded regions</h3>
          {regions.map((world) => (
            <div className="plain-row" key={world.map_id}>
              <span>{localizedText(world.named_region?.name, `Map ${world.map_id}`)}</span>
              <small>Region only · exact placements unavailable</small>
            </div>
          ))}
          {locations.map(({ node }) => (
            <Link
              className="connection-row"
              key={node.canonical_key}
              to={`/locations/${encodeURIComponent(node.canonical_key)}`}
            >
              <strong>{node.label}</strong>
              <ArrowRight size={16} />
            </Link>
          ))}
        </div>
      )}
      {paths.length > 0 && (
        <div className="subsection">
          <h3>Other ways to obtain</h3>
          {paths.map((path) => (
            <div className="plain-row" key={path.id}>
              <span>{localizedText(path.description, `Access path ${path.id}`)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
