import { ArrowRight, MapPin, Swords } from 'lucide-react';
import { Link } from '@/lib/router';
import { publicPath, questPath } from '@/lib/routeKey';

import { gameTextPlainText } from '../../data/gameText';
import { PlayerText, type PlayerDisplay } from '../dialogue/PlayerText';
import { EmptyInline } from '../ui/Feedback';

type Text = { content?: string; key?: string } | null;
type Acquisition = {
  banner_sources?: {
    view_id: number;
    banner_type: string;
    roles: string[];
    title?: Text;
    source: { file: string; row: number; game_version: string };
  }[];
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
  quest_rewards?: { quest: { game_quest_id: number; name: Text; slug?: string; href?: string } }[];
  quest_rewards_has_more?: boolean;
  locations?: { node: { canonical_key: string; label: string; slug?: string; href?: string } }[];
  harvest_world_maps?: { map_id: number; named_region?: { canonical_key?: string; name: Text } }[];
};

export function ItemAcquisition({ data, locale, display }: { data: Acquisition; locale: string; display?: PlayerDisplay }) {
  const maps = data.map_sources ?? [];
  const worlds = new Map<number, typeof maps>();
  for (const source of maps)
    worlds.set(source.map_id, [...(worlds.get(source.map_id) ?? []), source]);
  const shopNames = new Set(
    (data.shop_offers ?? []).map((shop) => gameTextPlainText(shop.shop_name, display)).filter(Boolean),
  );
  const paths = (data.acquisition_paths ?? []).filter(
    (path) => !shopNames.has(gameTextPlainText(path.description, display)),
  );
  const shops = data.shop_offers ?? [];
  const banners = data.banner_sources ?? [];
  const quests = data.quest_rewards ?? [];
  const regions = maps.length ? [] : (data.harvest_world_maps ?? []);
  const locations = data.locations ?? [];
  const name = (names: Record<string, string>) =>
    names[locale] || names.en || Object.values(names)[0];
  if (
    !maps.length &&
    !paths.length &&
    !shops.length &&
    !banners.length &&
    !quests.length &&
    !regions.length &&
    !locations.length
  )
    return <EmptyInline text="No other acquisition sources recorded yet." />;
  return (
    <div className="item-acquisition">
      {banners.length > 0 && (
        <div className="subsection">
          <h3>Convene banners</h3>
          {banners.map((banner) => (
            <div key={banner.view_id}>
              <div className="plain-row">
                <span>
                  <strong><PlayerText value={banner.title} fallback={banner.banner_type === 'permanent_weapon' ? 'Permanent weapon Convene' : `Weapon Convene ${banner.view_id}`} display={display} /></strong>
                  <small>{banner.roles.includes('featured') ? 'Featured weapon' :
                    banner.roles.includes('preview') ? 'Listed in banner preview' : 'Shown in banner artwork'}</small>
                </span>
                <small>Snapshot {banner.source.game_version}</small>
              </div>
              <small className="source-caption">Source: {banner.source.file}, row {banner.source.row}</small>
            </div>
          ))}
          <small>Banner references do not confirm current availability or drop rates.</small>
        </div>
      )}
      {maps.length > 0 && (
        <div className="subsection">
          <h3>On the map</h3>
          {[...worlds.entries()].map(([worldId, sources]) => (
            <details key={worldId} className="acquisition-world" open={worlds.size === 1}>
              <summary>
                <strong><PlayerText value={name(sources[0].map_names)} fallback="World map" display={display} /></strong>
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
                    <strong><PlayerText value={name(source.names)} fallback="Collection points" display={display} /></strong>
                    <small>
                      {source.kind === 'loot_preview' ? 'Possible drops' : 'Gathering'} ·{' '}
                      {source.count} locations
                      {source.hidden_count > 0 ? ` · ${source.hidden_count} hidden` : ''}
                    </small>
                    <small>
                      <PlayerText value={name(source.map_names)} fallback="World map" display={display} /> · {source.game_version}
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
              to={questPath(quest)}
              className="connection-row"
            >
              <div>
                <strong><PlayerText value={quest.name} fallback={`Quest ${quest.game_quest_id}`} display={display} /></strong>
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
                <strong><PlayerText value={shop.shop_name} fallback={`Shop ${shop.shop_id}`} display={display} /></strong>
                <small>
                  {shop.prices?.map((price, index) => (
                    <span key={index}>{index > 0 && ' + '}{price.amount} <PlayerText value={price.item?.label} fallback={`currency ${price.item_id}`} display={display} /></span>
                  ))}{' '}
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
              <span><PlayerText value={world.named_region?.name} fallback={`Map ${world.map_id}`} display={display} /></span>
              <small>Region only · exact placements unavailable</small>
            </div>
          ))}
          {locations.map(({ node }) => (
            <Link
              className="connection-row"
              key={node.canonical_key}
              to={publicPath("locations", node)}
            >
              <strong><PlayerText value={node.label} display={display} /></strong>
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
              <span><PlayerText value={path.description} fallback={`Access path ${path.id}`} display={display} /></span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
