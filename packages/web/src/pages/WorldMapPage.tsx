import 'leaflet/dist/leaflet.css';
import '../styles/world-map.css';

import L from 'leaflet';
import { Check, CircleCheck, FoldVertical, Link2, RotateCcw, Undo2, X } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { api } from '../api/client';
import { iconNode, ObjectIcon } from '../components/MapIcons';
import { APP_SETTINGS } from '../config/settings';
import { acquisitionMarkers, markerType } from '../data/mapAcquisition';
import { latestMapRoots } from '../data/mapSnapshots';
import { useLocale } from '../hooks/useLocale';
import { useMapPreferences } from '../hooks/useMapPreferences';
import { useMapProgress } from '../hooks/useMapProgress';
import { compactMapLink, type MapPreferences } from '../state/mapPreferences';
import { canMarkFound, mapProgressKey } from '../state/mapProgress';
type Names = Record<string, string>;
type Place = {
  id: number;
  names: Names;
  group_names?: Names;
  bounds: number[];
};
type Atlas = {
  icons?: Record<string, { url: string }>;
  id: number;
  game_version: string;
  asset_job_id: string;
  game_map_id: number;
  layer: string;
  grid_bounds: number[];
  metadata: {
    catalog?: {
      names?: Names;
      locations?: Place[];
      floors?: Record<
        string,
        { floor: number; group_id: number; group_names?: Names; names: Names }
      >;
    };
  };
  preview?: { url: string };
  tiles?: { x: number; y: number; image: { url: string } | null }[];
};
type Marker = {
  id: number;
  entity_id: number;
  category: string;
  blueprint_type: string;
  world: number[];
  metadata: {
    names?: Names;
    description?: Names;
    type_key?: string;
    area_ids?: number[];
    floor?: number;
    hidden?: boolean;
    condition_id?: number;
    icon_source?: string;
    item_id?: number;
    drop_item_ids?: number[];
    resource_group?: string;
  };
};
const categories: Record<string, [string, string]> = {
  hologram: ['Tactical holograms', '#f18b7e'],
  combat_activity: ['Dream patrols', '#e3c176'],
  teleport: ['Fast travel', '#82cbcd'],
  tacet_field: ['Tacet fields', '#b9a3ee'],
  chest: ['Chests', '#e3c176'],
  resource: ['Plants & materials', '#9bd9c0'],
  collectible: ['Collectibles', '#edabcf'],
  boss: ['Bosses', '#f18b7e'],
  monster: ['Enemies', '#c29b8b'],
  shop: ['Shops & services', '#c6dbec'],
  activity: ['Activities', '#d3b5ef'],
  treasure_spot: ['Treasure areas', '#dac78f'],
  exploration: ['Other map marks', '#b5c4c2'],
};
const menuGroups = [
  {
    key: 'featured',
    title: 'Featured',
    categories: ['teleport', 'chest', 'collectible'],
  },
  {
    key: 'battle',
    title: 'Battle',
    categories: ['boss', 'tacet_field', 'monster', 'hologram', 'combat_activity'],
  },
  {
    key: 'activities',
    title: 'Activities & exploration',
    categories: ['activity', 'exploration', 'treasure_spot'],
  },
  {
    key: 'unidentified',
    title: 'Unidentified collectibles',
    categories: ['collectible'],
  },
  { key: 'services', title: 'Shops & services', categories: ['shop'] },
  { key: 'ascension', title: 'Ascension materials', categories: ['resource'] },
  { key: 'ore', title: 'Ore', categories: ['resource'] },
  { key: 'gathering', title: 'Other resources', categories: ['resource'] },
];
const mapSettings = APP_SETTINGS.map;

const position = (world: number[]) =>
  L.latLng(
    (-world[1] / mapSettings.worldUnitsPerTile) * mapSettings.tilePixels,
    (world[0] / mapSettings.worldUnitsPerTile) * mapSettings.tilePixels,
  );
const bounds = (a: Atlas) =>
  L.latLngBounds(
    [
      (a.grid_bounds[1] - 1) * mapSettings.tilePixels,
      (a.grid_bounds[0] - 1) * mapSettings.tilePixels,
    ],
    [a.grid_bounds[3] * mapSettings.tilePixels, a.grid_bounds[2] * mapSettings.tilePixels],
  );
function MapCanvas({
  base,
  floors,
  active,
  opacity,
  markers,
  foundIds,
  selected,
  onSelect,
  focus,
  resetView,
  resetLocation,
  onVisible,
  onCluster,
}: {
  base: Atlas;
  floors: Atlas[];
  active: string;
  opacity: number;
  markers: Marker[];
  foundIds: ReadonlySet<number>;
  selected: Marker | null;
  onSelect: (m: Marker) => void;
  focus: Place | Marker | null;
  resetView: number;
  resetLocation: Place | undefined;
  onVisible: (ids: Set<number>) => void;
  onCluster: (markers: Marker[]) => void;
}) {
  const element = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const objects = useRef<L.LayerGroup | null>(null);
  const [zoom, setZoom] = useState<number>(mapSettings.initialZoom);
  const [view, setView] = useState(0);
  const [tileError, setTileError] = useState(false);
  const lastResetView = useRef(0);
  useEffect(() => {
    const instance = L.map(element.current!, {
      crs: L.CRS.Simple,
      minZoom: mapSettings.minZoom,
      maxZoom: mapSettings.maxZoom,
      preferCanvas: true,
      attributionControl: false,
    });
    map.current = instance;
    for (const [pane, z] of mapSettings.panes) instance.createPane(pane).style.zIndex = String(z);
    objects.current = L.layerGroup().addTo(instance);
    const updateMinZoom = () =>
      instance.setMinZoom(
        Math.max(
          mapSettings.minZoom,
          instance.getBoundsZoom(bounds(base)) - mapSettings.overviewZoomOutLevels,
        ),
      );
    updateMinZoom();
    instance.fitBounds(bounds(base), {
      padding: [mapSettings.initialFitPadding, mapSettings.initialFitPadding],
    });
    const update = () => {
      setZoom(instance.getZoom());
      setView((v) => v + 1);
    };
    instance.on('moveend zoomend', update);
    const resize = new ResizeObserver(() => {
      instance.invalidateSize();
      updateMinZoom();
    });
    resize.observe(element.current!);
    return () => {
      resize.disconnect();
      instance.remove();
      map.current = null;
    };
  }, [base.id]);
  useEffect(() => {
    const instance = map.current!;
    const layers: L.Layer[] = [];
    setTileError(false);
    for (const atlas of [base, ...floors.filter((f) => f.layer === active)]) {
      const alpha = atlas.id === base.id && active ? opacity / 100 : 1;
      if (atlas.preview) {
        const preview = L.imageOverlay(atlas.preview.url, bounds(atlas), {
          pane: atlas.id === base.id ? 'surface-preview' : 'floor-preview',
          opacity: alpha,
          interactive: false,
        }).addTo(instance);
        preview.on('error', () => setTileError(true));
        layers.push(preview);
      }
      if (zoom >= mapSettings.detailMinZoom && atlas.tiles) {
        const lookup = new Map(atlas.tiles.map((t) => [`${t.x},${t.y}`, t.image?.url]));
        const Grid = L.GridLayer.extend({
          createTile(coords: L.Coords, done: L.DoneCallback) {
            const image = document.createElement('img');
            const url = lookup.get(`${coords.x + 1},${-coords.y}`);
            image.alt = '';
            if (url) {
              image.onload = () => done(undefined, image);
              image.onerror = () => {
                setTileError(true);
                done(undefined, image);
              };
              image.src = url;
            } else queueMicrotask(() => done(undefined, image));
            return image;
          },
        });
        layers.push(
          new (Grid as typeof L.GridLayer)({
            tileSize: mapSettings.tilePixels,
            pane: atlas.id === base.id ? 'surface-detail' : 'floor-detail',
            minNativeZoom: mapSettings.tileNativeZoom,
            maxNativeZoom: mapSettings.tileNativeZoom,
            minZoom: mapSettings.detailMinZoom,
            maxZoom: mapSettings.maxZoom,
            opacity: alpha,
            bounds: bounds(atlas),
          }).addTo(instance),
        );
      }
    }
    return () => {
      layers.forEach((layer) => instance.removeLayer(layer));
    };
  }, [base, floors, active, opacity, zoom]);
  useEffect(() => {
    const instance = map.current!;
    const group = objects.current!;
    group.clearLayers();
    const visible = markers.filter((m) => instance.getBounds().contains(position(m.world)));
    onVisible(new Set(visible.map((m) => m.id)));
    const cells = new Map<string, Marker[]>();
    for (const marker of visible) {
      const point = instance.latLngToContainerPoint(position(marker.world));
      const size =
        zoom < mapSettings.clusterSplitZoom
          ? mapSettings.clusterCellPixels
          : mapSettings.closeClusterCellPixels;
      const key =
        selected?.id === marker.id
          ? `m${marker.id}`
          : `${Math.floor(point.x / size)},${Math.floor(point.y / size)}:${foundIds.has(marker.id) ? 'found' : 'remaining'}`;
      const cell = cells.get(key) ?? [];
      cell.push(marker);
      cells.set(key, cell);
    }
    for (const cell of cells.values()) {
      const marker = cell[0];
      if (cell.length > 1) {
        const center = L.latLng(
          cell.reduce((sum, m) => sum + position(m.world).lat, 0) / cell.length,
          cell.reduce((sum, m) => sum + position(m.world).lng, 0) / cell.length,
        );
        const label = iconNode(marker, base.icons);
        const count = document.createElement('b');
        count.className = 'atlas-cluster-count';
        count.textContent = String(cell.length);
        label.append(count);
        L.marker(center, {
          icon: L.divIcon({
            className: `atlas-point atlas-cluster${foundIds.has(marker.id) ? ' is-found' : ''}`,
            html: label,
            iconSize: [mapSettings.clusterSize, mapSettings.clusterSize],
          }),
          title: `${cell.length} objects. Zoom in to explore.`,
          opacity: foundIds.has(marker.id) ? mapSettings.foundMarkerOpacity : 1,
        })
          .addTo(group)
          .on('click', () => {
            if (zoom >= mapSettings.maxZoom) onCluster(cell);
            else
              instance.setView(
                center,
                Math.min(mapSettings.maxZoom, zoom + mapSettings.clusterZoomStep),
                {
                  animate: false,
                },
              );
          });
        continue;
      }
      const isSelected = selected?.id === marker.id;
      const dot = L.marker(position(marker.world), {
        icon: L.divIcon({
          className: `atlas-point${isSelected ? ' selected' : ''}${foundIds.has(marker.id) ? ' is-found' : ''}`,
          html: iconNode(marker, base.icons),
          iconSize: [mapSettings.markerSize, mapSettings.markerSize],
          iconAnchor: [mapSettings.markerSize / 2, mapSettings.markerSize / 2],
        }),
        title:
          (marker.metadata.names?.en ?? categories[marker.category]?.[0] ?? 'Map object') +
          (foundIds.has(marker.id) ? ' · Found' : ''),
        opacity: foundIds.has(marker.id) ? mapSettings.foundMarkerOpacity : 1,
      }).addTo(group);
      const tooltip = document.createElement('span');
      tooltip.textContent =
        cell.length > 1
          ? `${cell.length} objects — zoom in`
          : (marker.metadata.names?.en ?? categories[marker.category]?.[0] ?? 'Map object');
      dot.bindTooltip(tooltip);
      dot.on('click', () => {
        if (cell.length > 1)
          instance.setView(
            position(marker.world),
            Math.min(mapSettings.maxZoom, instance.getZoom() + mapSettings.markerZoomStep),
          );
        else onSelect(marker);
      });
    }
  }, [markers, foundIds, selected, zoom, view, onSelect, onVisible, onCluster, base.icons]);
  useEffect(() => {
    if (!focus || !map.current) return;
    if ('world' in focus) {
      map.current.setView(
        position(focus.world),
        Math.max(mapSettings.markerFocusMinZoom, map.current.getZoom()),
        {
          animate: false,
        },
      );
      if (element.current && element.current.clientWidth <= mapSettings.compactViewportWidth) {
        map.current.panBy(
          [0, element.current.clientHeight * mapSettings.markerFocusVerticalOffset],
          { animate: false },
        );
      }
    } else
      map.current.fitBounds(
        L.latLngBounds(
          position([focus.bounds[0], focus.bounds[1]]),
          position([focus.bounds[2], focus.bounds[3]]),
        ),
        {
          padding: [mapSettings.locationFitPadding, mapSettings.locationFitPadding],
          maxZoom: mapSettings.locationMaxZoom,
          animate: false,
        },
      );
  }, [focus]);
  useEffect(() => {
    if (resetView === lastResetView.current || !map.current) return;
    lastResetView.current = resetView;
    const target = resetLocation
      ? L.latLngBounds(
          position([resetLocation.bounds[0], resetLocation.bounds[1]]),
          position([resetLocation.bounds[2], resetLocation.bounds[3]]),
        )
      : bounds(base);
    const padding = resetLocation ? mapSettings.locationFitPadding : mapSettings.initialFitPadding;
    map.current.fitBounds(target, {
      padding: [padding, padding],
      maxZoom: mapSettings.locationMaxZoom,
      animate: false,
    });
  }, [resetView, resetLocation, base]);
  return (
    <div className="atlas-canvas" ref={element} aria-label="Interactive world map">
      {' '}
      {tileError && (
        <div className="atlas-warning" role="status">
          {' '}
          Some map images could not load. Reload to retry.{' '}
        </div>
      )}{' '}
    </div>
  );
}
export function WorldMapPage() {
  const locale = useLocale();
  const [params, setParams] = useSearchParams();
  const [filters, setFilters] = useMapPreferences(params, Object.keys(categories).join(','));
  const { found, showFound, storageError, setFound, setShowFound } = useMapProgress();
  const [shareStatus, setShareStatus] = useState('');
  const openedMarker = useRef('');
  const openedSource = useRef('');
  useEffect(() => {
    const compact = compactMapLink(params);
    if (compact.toString() !== params.toString()) setParams(compact, { replace: true });
  }, [params, setParams]);
  const [maps, setMaps] = useState<Atlas[]>([]);
  const [base, setBase] = useState<Atlas | null>(null);
  const [floors, setFloors] = useState<Atlas[]>([]);
  const [markers, setMarkers] = useState<Marker[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  const search = filters.q ?? '';
  const [selected, setSelected] = useState<Marker | null>(null);
  const [cluster, setCluster] = useState<Marker[]>([]);
  const [focus, setFocus] = useState<Place | Marker | null>(null);
  const [resetView, setResetView] = useState(0);
  const searchInput = useRef<HTMLInputElement>(null);
  const [visible, setVisible] = useState<Set<number>>(new Set());
  const [listLimit, setListLimit] = useState<number>(mapSettings.listPageSize);
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(
    () => new Set(menuGroups.filter((group) => group.key !== 'featured').map((group) => group.key)),
  );
  const toggleGroup = (key: string) =>
    setCollapsedGroups((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const name = (names?: Names, fallback = '') =>
    names?.[locale] || names?.en || Object.values(names ?? {})[0] || fallback;
  const change = (key: keyof MapPreferences, value: string) => {
    setFilters((current) => {
      const next = { ...current };
      if (value || key === 'hide') next[key] = value;
      else delete next[key];
      return next;
    });
  };
  const chooseMap = (id: string) => {
    setFilters((current) => ({ map: id, hide: current.hide ?? Object.keys(categories).join(',') }));
    setParams({}, { replace: true });
    setFocus(null);
    setSelected(null);
    setCluster([]);
  };
  const mapId = params.get('map') ?? filters.map;
  const active = filters.floor ?? '';
  const area = Number(filters.area ?? 0);
  const disabled = (filters.hide ?? '').split(',');
  const opacityValue = Number(filters.opacity ?? mapSettings.surfaceOpacityPercent);
  const opacity = Number.isFinite(opacityValue)
    ? Math.min(100, Math.max(0, opacityValue))
    : mapSettings.surfaceOpacityPercent;
  const unknown = filters.unknown !== '0';
  const includeHidden = filters.hidden === '1';
  useEffect(() => {
    const previous = document.title;
    document.title = 'Interactive map — Solaris Atlas';
    const timer = window.setInterval(() => setRetry((n) => n + 1), mapSettings.signedUrlRefreshMs);
    return () => {
      document.title = previous;
      window.clearInterval(timer);
    };
  }, []);
  useEffect(() => {
    let cancelled = false;
    api<Atlas[]>('/maps')
      .then((data) => {
        if (!cancelled) {
          setMaps(data);
          if (!data.length) setLoading(false);
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setError(e.message);
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [retry]);
  const roots = useMemo(() => latestMapRoots(maps), [maps]);
  const requestedMap = maps.find((m) => !m.layer.startsWith('floor:') && String(m.id) === mapId);
  // Explicit links retain their snapshot and marker IDs. Saved preferences follow
  // the newest available data for the same region/layer.
  const chosen =
    (params.has('map') ? requestedMap : undefined) ??
    roots.find(
      (m) => m.game_map_id === requestedMap?.game_map_id && m.layer === requestedMap.layer,
    ) ??
    roots.find((m) => m.game_map_id === mapSettings.defaultGameMapId) ??
    roots[0];
  const archivedMap = chosen && !roots.some((m) => m.id === chosen.id);
  const regionOptions = archivedMap ? [chosen, ...roots] : roots;
  useEffect(() => {
    if (!chosen) return;
    let cancelled = false;
    const controller = new AbortController();
    setLoading(true);
    setError('');
    setSelected(null);
    setMarkers([]);
    setBase(null);
    setFloors([]);
    const load = async () => {
      const manifest = await api<Atlas>(`/maps/${chosen.id}`, {
        signal: controller.signal,
      });
      if (cancelled) return;
      setBase(manifest);
      const result: Marker[] = [];
      let after: number | null = 0;
      while (after !== null) {
        const page: { items: Marker[]; next_after_id: number | null } = await api(
          `/maps/${chosen.id}/markers?compact=true&include_hidden=true&limit=${mapSettings.markerPageSize}&after_id=${after}`,
          { signal: controller.signal },
        );
        if (cancelled) return;
        result.push(...page.items);
        after = page.next_after_id;
      }
      setMarkers(result);
      setLoading(false);
    };
    load().catch((e) => {
      if (!cancelled) {
        setError(e.message);
        setLoading(false);
      }
    });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [chosen?.id, retry]);
  useEffect(() => {
    if (!active || !chosen) {
      setFloors([]);
      return;
    }
    let cancelled = false;
    setFloors([]);
    const target = maps.find(
      (m) =>
        m.asset_job_id === chosen.asset_job_id &&
        m.game_map_id === chosen.game_map_id &&
        m.game_version === chosen.game_version &&
        m.layer === active,
    );
    if (target)
      api<Atlas>(`/maps/${target.id}`)
        .then((m) => {
          if (!cancelled) {
            setFloors([m]);
            setFocus({
              id: m.id,
              names: {},
              bounds: [
                (m.grid_bounds[0] - 1) * mapSettings.worldUnitsPerTile,
                -m.grid_bounds[3] * mapSettings.worldUnitsPerTile,
                m.grid_bounds[2] * mapSettings.worldUnitsPerTile,
                -(m.grid_bounds[1] - 1) * mapSettings.worldUnitsPerTile,
              ],
            });
          }
        })
        .catch((e) => {
          if (!cancelled) setError(e.message);
        });
    return () => {
      cancelled = true;
    };
  }, [active, chosen?.id, maps, retry]);
  const sourceItem = Number(params.get('item'));
  const sourceId = Number(params.get('source'));
  const sourceMarkers = useMemo(
    () => acquisitionMarkers(markers, sourceItem, sourceId),
    [markers, sourceItem, sourceId],
  );
  const sourceMarkerIds = useMemo(() => new Set(sourceMarkers.map((m) => m.id)), [sourceMarkers]);
  useEffect(() => {
    if (!sourceItem || !sourceId) {
      openedSource.current = '';
      return;
    }
    if (loading || !base) return;
    const key = `${base.id}:${sourceItem}:${sourceId}`;
    if (openedSource.current === key) return;
    openedSource.current = key;
    if (!sourceMarkers.length) {
      setShareStatus('These acquisition sources are no longer available on this map.');
      return;
    }
    const type = markerType(sourceMarkers[0]);
    const category = sourceMarkers[0].category;
    setFilters((current) => ({
      ...current,
      map: String(base.id),
      area: '',
      q: '',
      floor: '',
      hidden: sourceMarkers.some((m) => m.metadata.hidden) ? '1' : '',
      unknown: '1',
      hide: [
        ...Object.keys(categories).filter((key) => key !== category),
        ...new Set(
          markers.filter((m) => m.category === category && markerType(m) !== type).map(markerType),
        ),
      ].join(','),
    }));
    setSelected(null);
    setCluster([]);
    setCollapsedGroups(
      (current) =>
        new Set(
          [...current].filter(
            (key) => !menuGroups.find((group) => group.key === key)?.categories.includes(category),
          ),
        ),
    );
    setFocus({
      id: sourceId,
      names: sourceMarkers[0].metadata.names ?? {},
      bounds: [
        Math.min(...sourceMarkers.map((m) => m.world[0])),
        Math.min(...sourceMarkers.map((m) => m.world[1])),
        Math.max(...sourceMarkers.map((m) => m.world[0])),
        Math.max(...sourceMarkers.map((m) => m.world[1])),
      ],
    });
    setShareStatus('');
  }, [loading, base, sourceItem, sourceId, sourceMarkers, markers, setFilters]);
  const markerId = params.get('marker');
  useEffect(() => {
    if (loading || !base || !markerId) return;
    const key = `${base.id}:${markerId}`;
    if (openedMarker.current === key) return;
    openedMarker.current = key;
    const marker = markers.find((item) => String(item.id) === markerId);
    if (!marker) {
      setShareStatus('This marker is no longer available on this map.');
      return;
    }
    setFilters((current) => ({
      ...current,
      map: String(base.id),
      area: '',
      q: '',
      floor: '',
      hidden: marker.metadata.hidden ? '1' : (current.hidden ?? ''),
    }));
    setSelected(marker);
    setCluster([]);
    setFocus(marker);
    setShareStatus('');
  }, [markers, loading, base?.id, markerId]);
  useEffect(() => {
    setShareStatus('');
  }, [selected?.id]);
  useEffect(() => {
    if (shareStatus !== 'Link copied.') return;
    const timer = window.setTimeout(
      () => setShareStatus(''),
      APP_SETTINGS.presentation.feedbackDurationMs,
    );
    return () => window.clearTimeout(timer);
  }, [shareStatus]);
  const markerLink =
    selected && chosen
      ? `${window.location.origin}/map?map=${chosen.id}&marker=${selected.id}`
      : '';
  const copyMarkerLink = async () => {
    try {
      await navigator.clipboard.writeText(markerLink);
      setShareStatus('Link copied.');
    } catch {
      setShareStatus('Could not copy the link. Please try again.');
    }
  };
  const locations = base?.metadata.catalog?.locations ?? [];
  useEffect(() => {
    if (sourceItem && sourceId) return;
    const location = locations.find((l) => l.id === area);
    if (location) setFocus(location);
    else if (markerId && selected && String(selected.id) === markerId) setFocus(selected);
    else if (base)
      setFocus({
        id: base.id,
        names: {},
        bounds: [
          (base.grid_bounds[0] - 1) * mapSettings.worldUnitsPerTile,
          -base.grid_bounds[3] * mapSettings.worldUnitsPerTile,
          base.grid_bounds[2] * mapSettings.worldUnitsPerTile,
          -(base.grid_bounds[1] - 1) * mapSettings.worldUnitsPerTile,
        ],
      });
  }, [area, base?.id, sourceItem, sourceId]);
  const scoped = useMemo(
    () =>
      markers.filter(
        (m) =>
          (includeHidden || !m.metadata.hidden) &&
          (!area || m.metadata.area_ids?.includes(area)) &&
          (!active ||
            m.metadata.floor === Number(active.split(':')[1]) ||
            (unknown && !m.metadata.floor)),
      ),
    [markers, area, active, unknown, includeHidden],
  );
  const progressMapId = base?.game_map_id;
  const foundIds = useMemo(
    () =>
      new Set(
        markers
          .filter(
            (m) =>
              progressMapId !== undefined &&
              canMarkFound(m) &&
              found.has(mapProgressKey(progressMapId, m)),
          )
          .map((m) => m.id),
      ),
    [markers, progressMapId, found],
  );
  const collection = scoped.filter(canMarkFound);
  const foundCount = collection.filter((m) => foundIds.has(m.id)).length;
  const types = useMemo(() => {
    const groups = new Map<string, { marker: Marker; count: number }>();
    for (const m of scoped) {
      if (sourceItem && sourceId && !sourceMarkerIds.has(m.id)) continue;
      const key = markerType(m);
      const item = groups.get(key) ?? { marker: m, count: 0 };
      item.count++;
      groups.set(key, item);
    }
    return [...groups.entries()].sort(
      (a, b) =>
        ['teleport', 'chest', 'collectible'].indexOf(a[1].marker.category) -
          ['teleport', 'chest', 'collectible'].indexOf(b[1].marker.category) ||
        a[1].marker.category.localeCompare(b[1].marker.category) ||
        b[1].count - a[1].count,
    );
  }, [scoped, sourceItem, sourceId, sourceMarkerIds]);
  const filtered = useMemo(
    () =>
      scoped.filter(
        (m) =>
          (showFound || !foundIds.has(m.id)) &&
          (!sourceItem || !sourceId || sourceMarkerIds.has(m.id)) &&
          ((String(m.id) === markerId && selected?.id === m.id) ||
            (!disabled.includes(m.category) && !disabled.includes(markerType(m)))) &&
          (!search ||
            `${name(m.metadata.names)} ${m.metadata.names?.en ?? ''} ${categories[m.category]?.[0]} ${m.blueprint_type}`
              .toLowerCase()
              .includes(search.toLowerCase())),
      ),
    [
      scoped,
      filters.hide,
      search,
      locale,
      markerId,
      selected?.id,
      sourceItem,
      sourceId,
      sourceMarkerIds,
      showFound,
      foundIds,
    ],
  );
  const [lastType, setLastType] = useState('');
  const toggleType = (type: string, marker: Marker) => {
    setLastType(name(marker.metadata.names, marker.blueprint_type));
    if (disabled.includes(marker.category)) {
      const siblings = markers.filter((m) => m.category === marker.category).map(markerType);
      change(
        'hide',
        [
          ...new Set([
            ...disabled.filter((k) => k && k !== marker.category && k !== type),
            ...siblings.filter((k) => k !== type),
          ]),
        ].join(','),
      );
    } else toggle(type);
  };
  const toggle = (key: string) =>
    change(
      'hide',
      disabled.includes(key)
        ? disabled.filter((k) => k && k !== key).join(',')
        : [...disabled.filter(Boolean), key].join(','),
    );
  const floorOptions = maps.filter(
    (m) =>
      chosen &&
      m.asset_job_id === chosen.asset_job_id &&
      m.game_map_id === chosen.game_map_id &&
      m.game_version === chosen.game_version &&
      m.layer.startsWith('floor:'),
  );
  const objectName = (m: Marker) =>
    name(
      m.metadata.names,
      m.blueprint_type === 'MapMark'
        ? 'Unnamed map mark'
        : m.category === 'collectible'
          ? 'Unidentified collectible'
          : `${categories[m.category]?.[0] ?? 'Object'} · ${m.blueprint_type}`,
    );
  const visibleCluster = cluster.filter((m) => showFound || !foundIds.has(m.id));
  const selectedFound = selected ? foundIds.has(selected.id) : false;
  return (
    <section className="world-map-page">
      {' '}
      <aside className="atlas-sidebar">
        {' '}
        <div className="atlas-heading">
          {' '}
          <span className="eyebrow">SOLARIS / FIELD ATLAS</span> <h1>Interactive map</h1>{' '}
          <p>Find a place. Explore what’s there.</p>{' '}
        </div>{' '}
        <label>
          {' '}
          Region{' '}
          <select
            value={chosen?.id ?? ''}
            onChange={(e) => {
              chooseMap(e.target.value);
            }}
          >
            {' '}
            {regionOptions.map((m) => (
              <option key={m.id} value={m.id}>
                {' '}
                {name(m.metadata.catalog?.names, `Map ${m.game_map_id}`)}{' '}
                {archivedMap && m.id === chosen.id ? ` · Map data ${m.game_version}` : ''}
                {m.layer === 'gravity:2' ? ' · Inverted' : ''}{' '}
              </option>
            ))}{' '}
          </select>{' '}
        </label>{' '}
        <div className="atlas-menu-toolbar">
          {' '}
          <label>
            {' '}
            Location{' '}
            <select
              value={area}
              onChange={(e) => change('area', e.target.value === '0' ? '' : e.target.value)}
            >
              {' '}
              <option value="0">All locations</option>{' '}
              {[...new Set(locations.map((l) => name(l.group_names, 'Other locations')))].map(
                (group) => (
                  <optgroup key={group} label={group}>
                    {' '}
                    {locations
                      .filter((l) => name(l.group_names, 'Other locations') === group)
                      .map((l) => (
                        <option key={l.id} value={l.id}>
                          {' '}
                          {name(l.names, `Location ${l.id}`)}{' '}
                        </option>
                      ))}{' '}
                  </optgroup>
                ),
              )}{' '}
            </select>{' '}
          </label>{' '}
          <div className="atlas-search-field">
            <label htmlFor="map-object-search">Find an object</label>
            <div className="atlas-search-input">
              <input
                id="map-object-search"
                ref={searchInput}
                type="search"
                placeholder="Plant, chest, beacon…"
                value={search}
                onChange={(e) => {
                  change('q', e.target.value);
                  setListLimit(mapSettings.listPageSize);
                }}
              />
              {search && (
                <button
                  type="button"
                  className="search-clear"
                  aria-label="Clear object search"
                  onClick={() => {
                    change('q', '');
                    setListLimit(mapSettings.listPageSize);
                    searchInput.current?.focus();
                  }}
                >
                  <X size={14} aria-hidden="true" />
                </button>
              )}
            </div>
          </div>{' '}
        </div>{' '}
        {sourceItem > 0 && sourceId > 0 && (
          <div className="atlas-source-filter" role="status">
            <span>
              {loading
                ? 'Loading acquisition sources…'
                : `${sourceMarkers.length} acquisition locations`}
            </span>
            <button
              type="button"
              onClick={() => {
                openedSource.current = '';
                setParams({ map: String(base?.id ?? mapId) }, { replace: true });
              }}
            >
              Clear source filter
            </button>
          </div>
        )}
        <details className="atlas-levels" open={Boolean(active)}>
          {' '}
          <summary>Layers & display</summary>{' '}
          {chosen && <p className="muted">Map data · {chosen.game_version}</p>}
          <label className="atlas-check">
            {' '}
            <input
              type="checkbox"
              checked={includeHidden}
              onChange={(e) => change('hidden', e.target.checked ? '1' : '')}
            />{' '}
            Include hidden game placements{' '}
          </label>{' '}
          <label>
            {' '}
            Visible floor{' '}
            <div className="atlas-floor-buttons" role="group" aria-label="Map floors">
              {' '}
              <button aria-pressed={!active} onClick={() => change('floor', '')}>
                {' '}
                Surface{' '}
              </button>{' '}
              {[
                ...new Set(
                  floorOptions.map(
                    (m) => base?.metadata.catalog?.floors?.[m.layer.split(':')[1]]?.group_id,
                  ),
                ),
              ].map((group) => (
                <details
                  key={group}
                  className="atlas-floor-group"
                  open={floorOptions.some(
                    (m) =>
                      m.layer === active &&
                      base?.metadata.catalog?.floors?.[m.layer.split(':')[1]]?.group_id === group,
                  )}
                >
                  {' '}
                  <summary>
                    {' '}
                    {name(
                      base?.metadata.catalog?.floors?.[
                        floorOptions
                          .find(
                            (m) =>
                              base?.metadata.catalog?.floors?.[m.layer.split(':')[1]]?.group_id ===
                              group,
                          )
                          ?.layer.split(':')[1] ?? ''
                      ]?.group_names,
                      `Area ${group}`,
                    )}{' '}
                  </summary>{' '}
                  {floorOptions
                    .filter(
                      (m) =>
                        base?.metadata.catalog?.floors?.[m.layer.split(':')[1]]?.group_id === group,
                    )
                    .map((m) => (
                      <button
                        key={m.id}
                        aria-pressed={active === m.layer}
                        onClick={() => change('floor', m.layer)}
                      >{`Floor ${base?.metadata.catalog?.floors?.[m.layer.split(':')[1]]?.floor ?? m.layer.split(':')[1]}`}</button>
                    ))}{' '}
                </details>
              ))}{' '}
            </div>{' '}
          </label>{' '}
          {active && (
            <>
              {' '}
              <label>
                {' '}
                Surface opacity <output>{opacity}%</output>{' '}
                <input
                  type="range"
                  min="0"
                  max="100"
                  value={opacity}
                  onChange={(e) => change('opacity', e.target.value)}
                />{' '}
              </label>{' '}
              <label className="atlas-check">
                {' '}
                <input
                  type="checkbox"
                  checked={unknown}
                  onChange={(e) => change('unknown', e.target.checked ? '' : '0')}
                />{' '}
                Include objects with unknown floor{' '}
              </label>{' '}
            </>
          )}{' '}
          <small> Floor placement is shown only where the game records it. </small>{' '}
        </details>{' '}
        {search && (
          <div className="atlas-search-result">
            {' '}
            <span>
              {' '}
              {
                types.filter(([, item]) =>
                  `${objectName(item.marker)} ${item.marker.metadata.names?.en ?? ''} ${categories[item.marker.category]?.[0]} ${item.marker.blueprint_type}`
                    .toLowerCase()
                    .includes(search.toLowerCase()),
                ).length
              }{' '}
              matching types{' '}
            </span>{' '}
            <button onClick={() => change('q', '')}>Clear search</button>{' '}
          </div>
        )}{' '}
        <div className="atlas-global-controls" aria-label="All map markers">
          {' '}
          <span>Map markers</span>{' '}
          <div>
            {' '}
            <button disabled={loading} onClick={() => change('hide', '')}>
              {' '}
              Show all{' '}
            </button>{' '}
            <button
              disabled={loading}
              onClick={() => {
                change(
                  'hide',
                  [
                    ...new Set([
                      ...Object.keys(categories),
                      ...markers.map((marker) => marker.category),
                    ]),
                  ].join(','),
                );
                setSelected(null);
                setCluster([]);
              }}
            >
              {' '}
              Hide all{' '}
            </button>{' '}
          </div>{' '}
          <button
            className="atlas-collapse-all"
            title="Collapse groups"
            aria-label="Collapse groups"
            onClick={() => setCollapsedGroups(new Set(menuGroups.map((group) => group.key)))}
          >
            <FoldVertical size={16} aria-hidden="true" />
          </button>{' '}
        </div>{' '}
        <section className="atlas-collection" aria-label="Collection progress">
          <div className="atlas-collection-heading">
            <span>Collection progress</span>
            <strong aria-live="polite">
              {loading
                ? 'Loading…'
                : `${foundCount.toLocaleString()} / ${collection.length.toLocaleString()} found`}
            </strong>
          </div>
          <progress
            value={foundCount}
            max={collection.length || 1}
            aria-label="Collected objects in this area"
          />
          <label className="atlas-check">
            <input
              type="checkbox"
              checked={showFound}
              onChange={(event) => setShowFound(event.target.checked)}
            />
            Show found markers
          </label>
          <small>Chests & collectibles · saved in this browser</small>
          {storageError && (
            <p className="atlas-progress-error" role="alert">
              Browser storage is unavailable. Changes are kept for this session only.
            </p>
          )}
        </section>
        {menuGroups.map((group) => {
          const entries = types.filter(
            ([, item]) =>
              group.categories.includes(item.marker.category) &&
              (group.key === 'unidentified'
                ? !item.marker.metadata.names?.en
                : group.key !== 'featured' ||
                  item.marker.category !== 'collectible' ||
                  Boolean(item.marker.metadata.names?.en)) &&
              (item.marker.category !== 'resource' ||
                (item.marker.metadata.resource_group ?? 'gathering') === group.key) &&
              (!search ||
                `${objectName(item.marker)} ${item.marker.metadata.names?.en ?? ''} ${categories[item.marker.category]?.[0]} ${item.marker.blueprint_type}`
                  .toLowerCase()
                  .includes(search.toLowerCase())),
          );
          if (!entries.length) return null;
          const keys = entries.map(([key]) => key);
          const selectAll = () => {
            const excluded = new Set(disabled.filter(Boolean));
            for (const category of group.categories)
              if (excluded.has(category)) {
                excluded.delete(category);
                markers
                  .filter((m) => m.category === category)
                  .forEach((m) => excluded.add(markerType(m)));
              }
            keys.forEach((key) => excluded.delete(key));
            change('hide', [...excluded].join(','));
          };
          return (
            <section className="atlas-menu-group" key={group.key}>
              {' '}
              <header
                onClick={(event) => {
                  if (!(event.target as Element).closest('button')) toggleGroup(group.key);
                }}
              >
                {' '}
                <h2>
                  {' '}
                  <button
                    className="atlas-group-toggle"
                    aria-expanded={!collapsedGroups.has(group.key)}
                    aria-controls={`atlas-group-${group.key}`}
                    onClick={() => toggleGroup(group.key)}
                  >
                    {' '}
                    <span aria-hidden="true">
                      {' '}
                      {collapsedGroups.has(group.key) ? '+' : '−'}{' '}
                    </span>{' '}
                    {group.title}{' '}
                  </button>{' '}
                </h2>{' '}
                <div>
                  {' '}
                  <button onClick={selectAll} aria-label={`Select all ${group.title}`}>
                    {' '}
                    Select all{' '}
                  </button>{' '}
                  <button
                    onClick={() =>
                      change('hide', [...new Set([...disabled.filter(Boolean), ...keys])].join(','))
                    }
                    aria-label={`Clear ${group.title}`}
                  >
                    {' '}
                    Clear{' '}
                  </button>{' '}
                </div>{' '}
              </header>{' '}
              <div
                id={`atlas-group-${group.key}`}
                hidden={collapsedGroups.has(group.key)}
                className={
                  group.key === 'gathering' ? 'atlas-type-grid compact' : 'atlas-type-grid'
                }
              >
                {' '}
                {entries.map(([type, item]) => {
                  const enabled =
                    !disabled.includes(item.marker.category) && !disabled.includes(type);
                  const title =
                    item.marker.category === 'combat_activity'
                      ? 'Dream Patrol'
                      : objectName(item.marker);
                  return (
                    <button
                      key={type}
                      className={enabled ? 'atlas-type-tile enabled' : 'atlas-type-tile'}
                      aria-pressed={enabled}
                      aria-label={`${title}, ${item.count} locations`}
                      title={`${title} · ${item.count} locations`}
                      onClick={() => toggleType(type, item.marker)}
                      onFocus={() => setLastType(title)}
                    >
                      {' '}
                      <ObjectIcon marker={item.marker} icons={base?.icons} />{' '}
                      <span className="atlas-tile-name">{title}</span>{' '}
                      <span className="atlas-tile-count"> {item.count.toLocaleString()} </span>{' '}
                    </button>
                  );
                })}{' '}
              </div>{' '}
            </section>
          );
        })}{' '}
        {lastType && (
          <p className="atlas-last-type" role="status">
            {' '}
            {lastType}{' '}
          </p>
        )}{' '}
        <details className="atlas-object-list">
          {' '}
          <summary>
            {' '}
            In this view · {filtered.filter((m) => visible.has(m.id)).length.toLocaleString()}{' '}
          </summary>{' '}
          {filtered
            .filter((m) => visible.has(m.id))
            .slice(0, listLimit)
            .map((m) => (
              <button
                key={m.id}
                className={`${selected?.id === m.id ? 'selected' : ''}${foundIds.has(m.id) ? ' is-found' : ''}`}
                onClick={() => {
                  setSelected(m);
                  setFocus(m);
                }}
              >
                {' '}
                <ObjectIcon marker={m} icons={base?.icons} /> {objectName(m)}{' '}
                {foundIds.has(m.id) && <span className="atlas-found-label">Found</span>}
              </button>
            ))}{' '}
          {filtered.filter((m) => visible.has(m.id)).length > listLimit && (
            <button onClick={() => setListLimit((n) => n + mapSettings.listPageSize)}>
              {' '}
              Show more objects{' '}
            </button>
          )}{' '}
          {!loading && !filtered.length && (
            <p>No objects match. Clear the search, show all categories or show found markers.</p>
          )}{' '}
        </details>{' '}
      </aside>{' '}
      <div className="atlas-map-area">
        {' '}
        <nav className="atlas-region-rail" aria-label="Switch map region">
          {' '}
          {roots.map((m) => {
            const title =
              name(m.metadata.catalog?.names, `Map ${m.game_map_id}`) +
              (m.layer === 'gravity:2' ? ' · Inverted' : '');
            return (
              <button
                key={m.id}
                aria-label={title}
                title={title}
                aria-pressed={chosen?.id === m.id}
                onClick={() => {
                  chooseMap(String(m.id));
                }}
              >
                {' '}
                <span>
                  {' '}
                  {title
                    .split(/\s+/)
                    .map((word) => word[0])
                    .join('')
                    .slice(0, mapSettings.regionLabelCharacters)}{' '}
                </span>{' '}
                <span className="atlas-region-name">{title}</span>{' '}
              </button>
            );
          })}{' '}
        </nav>{' '}
        {!loading && !maps.length && !error && (
          <div className="atlas-error" role="status">
            {' '}
            No maps have been imported yet.{' '}
          </div>
        )}{' '}
        {base && (
          <MapCanvas
            base={base}
            floors={floors}
            active={active}
            opacity={opacity}
            markers={filtered}
            foundIds={foundIds}
            selected={selected}
            onSelect={setSelected}
            focus={focus}
            resetView={resetView}
            resetLocation={locations.find((location) => location.id === area)}
            onVisible={setVisible}
            onCluster={setCluster}
          />
        )}{' '}
        <div className="atlas-map-status">
          <div role="status">
            {' '}
            {loading
              ? 'Loading map objects…'
              : `${filtered.length.toLocaleString()} ${filtered.length === 1 ? 'object' : 'objects'}`}{' '}
            <span>Scroll to zoom · drag to explore</span>
          </div>
        </div>
        <button
          className="atlas-reset-view"
          title="Return to map"
          aria-label="Return to map"
          disabled={!base || loading}
          onClick={() => setResetView((value) => value + 1)}
        >
          <RotateCcw size={16} aria-hidden="true" />
        </button>{' '}
        {shareStatus && (
          <p className="atlas-share-status" role="status">
            {shareStatus}
          </p>
        )}
        {error && (
          <div className="atlas-error" role="alert">
            {' '}
            <p>{error}</p> <button onClick={() => setRetry((n) => n + 1)}>Retry</button>{' '}
          </div>
        )}{' '}
        {visibleCluster.length > 0 && (
          <article className="atlas-detail atlas-cluster-detail">
            {' '}
            <button
              className="atlas-close"
              aria-label="Close nearby objects"
              onClick={() => setCluster([])}
            >
              {' '}
              ×{' '}
            </button>{' '}
            <h2>{visibleCluster.length} objects here</h2>{' '}
            {visibleCluster.map((m) => (
              <button
                key={m.id}
                className={foundIds.has(m.id) ? 'is-found' : ''}
                onClick={() => {
                  setSelected(m);
                  setCluster([]);
                  setFocus(m);
                }}
              >
                {' '}
                <ObjectIcon marker={m} icons={base?.icons} /> {objectName(m)}{' '}
                {foundIds.has(m.id) && <span className="atlas-found-label">Found</span>}
              </button>
            ))}{' '}
          </article>
        )}{' '}
        {selected && visibleCluster.length === 0 && (
          <article className="atlas-detail atlas-marker-detail">
            {' '}
            <header className="atlas-detail-header">
              <div className="atlas-detail-title">
                {objectName(selected).toLowerCase() !==
                  categories[selected.category]?.[0].toLowerCase() && (
                  <span className="atlas-detail-category">
                    {categories[selected.category]?.[0] ?? 'Map object'}
                  </span>
                )}
                <h2>{objectName(selected)}</h2>
              </div>
              <button
                className="atlas-icon-button"
                aria-label="Copy marker link"
                title={shareStatus === 'Link copied.' ? 'Link copied' : 'Copy marker link'}
                onClick={copyMarkerLink}
              >
                {shareStatus === 'Link copied.' ? (
                  <Check size={16} aria-hidden="true" />
                ) : (
                  <Link2 size={16} aria-hidden="true" />
                )}
              </button>
              <button
                className="atlas-icon-button"
                aria-label="Close object details"
                title="Close details"
                onClick={() => {
                  setSelected(null);
                  setShareStatus('');
                }}
              >
                <X size={16} aria-hidden="true" />
              </button>
            </header>
            <div className="atlas-detail-body">
              {canMarkFound(selected) && (
                <div className="atlas-found-control">
                  <div className="atlas-found-state" role="status">
                    {selectedFound ? <CircleCheck size={15} aria-hidden="true" /> : null}
                    <span>{selectedFound ? 'Found' : 'Not found'}</span>
                    {selectedFound && !showFound && <small>Hidden from the map</small>}
                  </div>
                  <button
                    type="button"
                    className={`atlas-found-button${selectedFound ? ' is-found' : ''}`}
                    disabled={!base || loading}
                    onClick={() => {
                      if (base)
                        setFound(mapProgressKey(base.game_map_id, selected), !selectedFound);
                    }}
                  >
                    {selectedFound ? (
                      <Undo2 size={16} aria-hidden="true" />
                    ) : (
                      <Check size={16} aria-hidden="true" />
                    )}
                    {selectedFound ? 'Mark as not found' : 'Mark as found'}
                  </button>
                </div>
              )}
              {name(selected.metadata.description) && <p>{name(selected.metadata.description)}</p>}{' '}
              {Boolean(selected.metadata.condition_id) && (
                <p>This mark appears under an in-game condition.</p>
              )}{' '}
              <details>
                {' '}
                <summary>Position & source</summary>{' '}
                <p>
                  {selected.metadata.floor
                    ? `Recorded floor: ${selected.metadata.floor}`
                    : 'Floor not recorded'}
                </p>
                <small>Game placements may depend on progress or respawn state.</small>
                <p>
                  {' '}
                  X {selected.world[0].toFixed(0)} · Y {selected.world[1].toFixed(0)} · Z{' '}
                  {selected.world[2].toFixed(0)}{' '}
                </p>{' '}
                <p>
                  {' '}
                  {selected.blueprint_type} · {selected.entity_id}{' '}
                </p>{' '}
              </details>
            </div>
          </article>
        )}{' '}
      </div>{' '}
    </section>
  );
}
