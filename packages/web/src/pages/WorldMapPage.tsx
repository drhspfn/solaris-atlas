import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { api } from "../api/client";
import { useLocale } from "../hooks/useLocale";
import "../styles/world-map.css";
import { ObjectIcon, iconNode } from "../components/MapIcons";
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
    resource_group?: string;
  };
};
const categories: Record<string, [string, string]> = {
  hologram: ["Tactical holograms", "#f18b7e"],
  combat_activity: ["Dream patrols", "#e3c176"],
  teleport: ["Resonance beacons", "#82cbcd"],
  tacet_field: ["Tacet fields", "#b9a3ee"],
  chest: ["Chests", "#e3c176"],
  resource: ["Plants & materials", "#9bd9c0"],
  collectible: ["Collectibles", "#edabcf"],
  boss: ["Bosses", "#f18b7e"],
  monster: ["Enemies", "#c29b8b"],
  shop: ["Shops & services", "#c6dbec"],
  activity: ["Activities", "#d3b5ef"],
  treasure_spot: ["Treasure areas", "#dac78f"],
  exploration: ["Other map marks", "#b5c4c2"],
};
const menuGroups = [
  {
    key: "featured",
    title: "Featured",
    categories: ["teleport", "chest", "collectible"],
  },
  {
    key: "battle",
    title: "Battle",
    categories: [
      "boss",
      "tacet_field",
      "monster",
      "hologram",
      "combat_activity",
    ],
  },
  {
    key: "activities",
    title: "Activities & exploration",
    categories: ["activity", "exploration", "treasure_spot"],
  },
  {
    key: "unidentified",
    title: "Unidentified collectibles",
    categories: ["collectible"],
  },
  { key: "services", title: "Shops & services", categories: ["shop"] },
  { key: "ascension", title: "Ascension materials", categories: ["resource"] },
  { key: "ore", title: "Ore", categories: ["resource"] },
  { key: "gathering", title: "Other resources", categories: ["resource"] },
];
const markerType = (m: Marker) =>
  `${m.category}:${m.metadata.item_id ? `item:${m.metadata.item_id}` : m.category === "combat_activity" ? "dream-patrol" : m.metadata.names?.en || m.metadata.icon_source || m.metadata.type_key || m.blueprint_type}`;
const position = (world: number[]) =>
  L.latLng((-world[1] / 85000) * 256, (world[0] / 85000) * 256);
const bounds = (a: Atlas) =>
  L.latLngBounds(
    [(a.grid_bounds[1] - 1) * 256, (a.grid_bounds[0] - 1) * 256],
    [a.grid_bounds[3] * 256, a.grid_bounds[2] * 256],
  );
function MapCanvas({
  base,
  floors,
  active,
  opacity,
  markers,
  selected,
  onSelect,
  focus,
  onVisible,
  onCluster,
}: {
  base: Atlas;
  floors: Atlas[];
  active: string;
  opacity: number;
  markers: Marker[];
  selected: Marker | null;
  onSelect: (m: Marker) => void;
  focus: Place | Marker | null;
  onVisible: (ids: Set<number>) => void;
  onCluster: (markers: Marker[]) => void;
}) {
  const element = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const objects = useRef<L.LayerGroup | null>(null);
  const [zoom, setZoom] = useState(-2);
  const [view, setView] = useState(0);
  const [tileError, setTileError] = useState(false);
  useEffect(() => {
    const instance = L.map(element.current!, {
      crs: L.CRS.Simple,
      minZoom: -6,
      maxZoom: 2,
      preferCanvas: true,
      attributionControl: false,
    });
    map.current = instance;
    for (const [pane, z] of [
      ["surface-preview", 200],
      ["surface-detail", 220],
      ["floor-preview", 240],
      ["floor-detail", 260],
    ] as const)
      instance.createPane(pane).style.zIndex = String(z);
    objects.current = L.layerGroup().addTo(instance);
    instance.fitBounds(bounds(base), { padding: [25, 25] });
    const update = () => {
      setZoom(instance.getZoom());
      setView((v) => v + 1);
    };
    instance.on("moveend zoomend", update);
    const resize = new ResizeObserver(() => instance.invalidateSize());
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
          pane: atlas.id === base.id ? "surface-preview" : "floor-preview",
          opacity: alpha,
          interactive: false,
        }).addTo(instance);
        preview.on("error", () => setTileError(true));
        layers.push(preview);
      }
      if (zoom >= -1 && atlas.tiles) {
        const lookup = new Map(
          atlas.tiles.map((t) => [`${t.x},${t.y}`, t.image?.url]),
        );
        const Grid = L.GridLayer.extend({
          createTile(coords: L.Coords, done: L.DoneCallback) {
            const image = document.createElement("img");
            const url = lookup.get(`${coords.x + 1},${-coords.y}`);
            image.alt = "";
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
            tileSize: 256,
            pane: atlas.id === base.id ? "surface-detail" : "floor-detail",
            minNativeZoom: 0,
            maxNativeZoom: 0,
            minZoom: -1,
            maxZoom: 2,
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
    const visible = markers.filter((m) =>
      instance.getBounds().contains(position(m.world)),
    );
    onVisible(new Set(visible.map((m) => m.id)));
    const cells = new Map<string, Marker[]>();
    for (const marker of visible) {
      const point = instance.latLngToContainerPoint(position(marker.world));
      const size = zoom < 2 ? 42 : 20;
      const key =
        selected?.id === marker.id
          ? `m${marker.id}`
          : `${Math.floor(point.x / size)},${Math.floor(point.y / size)}`;
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
        const count = document.createElement("b");
        count.className = "atlas-cluster-count";
        count.textContent = String(cell.length);
        label.append(count);
        L.marker(center, {
          icon: L.divIcon({
            className: "atlas-point atlas-cluster",
            html: label,
            iconSize: [32, 32],
          }),
          title: `${cell.length} objects. Zoom in to explore.`,
        })
          .addTo(group)
          .on("click", () => {
            if (zoom >= 2) onCluster(cell);
            else
              instance.setView(center, Math.min(2, zoom + 1), {
                animate: false,
              });
          });
        continue;
      }
      const isSelected = selected?.id === marker.id;
      const dot = L.marker(position(marker.world), {
        icon: L.divIcon({
          className: isSelected ? "atlas-point selected" : "atlas-point",
          html: iconNode(marker, base.icons),
          iconSize: [30, 30],
          iconAnchor: [15, 15],
        }),
        title:
          marker.metadata.names?.en ??
          categories[marker.category]?.[0] ??
          "Map object",
      }).addTo(group);
      const tooltip = document.createElement("span");
      tooltip.textContent =
        cell.length > 1
          ? `${cell.length} objects — zoom in`
          : (marker.metadata.names?.en ??
            categories[marker.category]?.[0] ??
            "Map object");
      dot.bindTooltip(tooltip);
      dot.on("click", () => {
        if (cell.length > 1)
          instance.setView(
            position(marker.world),
            Math.min(2, instance.getZoom() + 2),
          );
        else onSelect(marker);
      });
    }
  }, [
    markers,
    selected,
    zoom,
    view,
    onSelect,
    onVisible,
    onCluster,
    base.icons,
  ]);
  useEffect(() => {
    if (!focus || !map.current) return;
    if ("world" in focus)
      map.current.setView(
        position(focus.world),
        Math.max(0, map.current.getZoom()),
        { animate: false },
      );
    else
      map.current.fitBounds(
        L.latLngBounds(
          position([focus.bounds[0], focus.bounds[1]]),
          position([focus.bounds[2], focus.bounds[3]]),
        ),
        { padding: [35, 35], maxZoom: 1, animate: false },
      );
  }, [focus]);
  return (
    <div
      className="atlas-canvas"
      ref={element}
      aria-label="Interactive world map"
    >
      {" "}
      {tileError && (
        <div className="atlas-warning" role="status">
          {" "}
          Some map images could not load. Reload to retry.{" "}
        </div>
      )}{" "}
    </div>
  );
}
export function WorldMapPage() {
  const locale = useLocale();
  const [params, setParams] = useSearchParams();
  const [maps, setMaps] = useState<Atlas[]>([]);
  const [base, setBase] = useState<Atlas | null>(null);
  const [floors, setFloors] = useState<Atlas[]>([]);
  const [markers, setMarkers] = useState<Marker[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  const search = params.get("q") ?? "";
  const [selected, setSelected] = useState<Marker | null>(null);
  const [cluster, setCluster] = useState<Marker[]>([]);
  const [focus, setFocus] = useState<Place | Marker | null>(null);
  const [visible, setVisible] = useState<Set<number>>(new Set());
  const [listLimit, setListLimit] = useState(60);
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(
    () =>
      new Set(
        menuGroups
          .filter((group) => group.key !== "featured")
          .map((group) => group.key),
      ),
  );
  const toggleGroup = (key: string) =>
    setCollapsedGroups((current) => {
      const next = new Set(current);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  const name = (names?: Names, fallback = "") =>
    names?.[locale] || names?.en || Object.values(names ?? {})[0] || fallback;
  const change = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    value ? next.set(key, value) : next.delete(key);
    setParams(next, { replace: key === "q" || key === "opacity" });
  };
  const mapId = params.get("map");
  const active = params.get("floor") ?? "";
  const area = Number(params.get("area") ?? 0);
  const disabled = (params.get("hide") ?? "").split(",");
  const opacity = Math.min(
    100,
    Math.max(0, Number(params.get("opacity") ?? 30)),
  );
  const unknown = params.get("unknown") !== "0";
  const includeHidden = params.get("hidden") === "1";
  useEffect(() => {
    const previous = document.title;
    document.title = "Interactive map — Solaris Atlas";
    const timer = window.setInterval(
      () => setRetry((n) => n + 1),
      45 * 60 * 1000,
    );
    return () => {
      document.title = previous;
      window.clearInterval(timer);
    };
  }, []);
  useEffect(() => {
    let cancelled = false;
    api<Atlas[]>("/maps")
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
  const roots = useMemo(
    () =>
      maps
        .filter((m) => !m.layer.startsWith("floor:"))
        .sort(
          (a, b) =>
            b.game_version.localeCompare(a.game_version, undefined, {
              numeric: true,
            }) || a.game_map_id - b.game_map_id,
        ),
    [maps],
  );
  const chosen =
    roots.find((m) => String(m.id) === mapId) ??
    roots.find((m) => m.game_map_id === 8) ??
    roots[0];
  useEffect(() => {
    if (!chosen) return;
    let cancelled = false;
    const controller = new AbortController();
    setLoading(true);
    setError("");
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
        const page: { items: Marker[]; next_after_id: number | null } =
          await api(
            `/maps/${chosen.id}/markers?compact=true&include_hidden=true&limit=5000&after_id=${after}`,
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
                (m.grid_bounds[0] - 1) * 85000,
                -m.grid_bounds[3] * 85000,
                m.grid_bounds[2] * 85000,
                -(m.grid_bounds[1] - 1) * 85000,
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
  const locations = base?.metadata.catalog?.locations ?? [];
  useEffect(() => {
    const location = locations.find((l) => l.id === area);
    if (location) setFocus(location);
    else if (base)
      setFocus({
        id: base.id,
        names: {},
        bounds: [
          (base.grid_bounds[0] - 1) * 85000,
          -base.grid_bounds[3] * 85000,
          base.grid_bounds[2] * 85000,
          -(base.grid_bounds[1] - 1) * 85000,
        ],
      });
  }, [area, base?.id]);
  const scoped = useMemo(
    () =>
      markers.filter(
        (m) =>
          (includeHidden || !m.metadata.hidden) &&
          (!area || m.metadata.area_ids?.includes(area)) &&
          (!active ||
            m.metadata.floor === Number(active.split(":")[1]) ||
            (unknown && !m.metadata.floor)),
      ),
    [markers, area, active, unknown, includeHidden],
  );
  const types = useMemo(() => {
    const groups = new Map<string, { marker: Marker; count: number }>();
    for (const m of scoped) {
      const key = markerType(m);
      const item = groups.get(key) ?? { marker: m, count: 0 };
      item.count++;
      groups.set(key, item);
    }
    return [...groups.entries()].sort(
      (a, b) =>
        ["teleport", "chest", "collectible"].indexOf(a[1].marker.category) -
          ["teleport", "chest", "collectible"].indexOf(b[1].marker.category) ||
        a[1].marker.category.localeCompare(b[1].marker.category) ||
        b[1].count - a[1].count,
    );
  }, [scoped]);
  const filtered = useMemo(
    () =>
      scoped.filter(
        (m) =>
          !disabled.includes(m.category) &&
          !disabled.includes(markerType(m)) &&
          (!search ||
            `${name(m.metadata.names)} ${m.metadata.names?.en ?? ""} ${categories[m.category]?.[0]} ${m.blueprint_type}`
              .toLowerCase()
              .includes(search.toLowerCase())),
      ),
    [scoped, params, search, locale],
  );
  const [lastType, setLastType] = useState("");
  const toggleType = (type: string, marker: Marker) => {
    setLastType(name(marker.metadata.names, marker.blueprint_type));
    if (disabled.includes(marker.category)) {
      const siblings = markers
        .filter((m) => m.category === marker.category)
        .map(markerType);
      change(
        "hide",
        [
          ...new Set([
            ...disabled.filter((k) => k && k !== marker.category && k !== type),
            ...siblings.filter((k) => k !== type),
          ]),
        ].join(","),
      );
    } else toggle(type);
  };
  const toggle = (key: string) =>
    change(
      "hide",
      disabled.includes(key)
        ? disabled.filter((k) => k && k !== key).join(",")
        : [...disabled.filter(Boolean), key].join(","),
    );
  const floorOptions = maps.filter(
    (m) =>
      chosen &&
      m.asset_job_id === chosen.asset_job_id &&
      m.game_map_id === chosen.game_map_id &&
      m.game_version === chosen.game_version &&
      m.layer.startsWith("floor:"),
  );
  const objectName = (m: Marker) =>
    name(
      m.metadata.names,
      m.blueprint_type === "MapMark"
        ? "Unnamed map mark"
        : m.category === "collectible"
          ? "Unidentified collectible"
          : `${categories[m.category]?.[0] ?? "Object"} · ${m.blueprint_type}`,
    );
  return (
    <section className="world-map-page">
      {" "}
      <aside className="atlas-sidebar">
        {" "}
        <div className="atlas-heading">
          {" "}
          <span className="eyebrow">SOLARIS / FIELD ATLAS</span>{" "}
          <h1>Interactive map</h1>{" "}
          <p>Find a place. Explore what’s there.</p>{" "}
        </div>{" "}
        <label>
          {" "}
          World & version{" "}
          <select
            value={chosen?.id ?? ""}
            onChange={(e) => {
              const next = new URLSearchParams();
              next.set("map", e.target.value);
              setParams(next);
              setFocus(null);
            }}
          >
            {" "}
            {roots.map((m) => (
              <option key={m.id} value={m.id}>
                {" "}
                {name(m.metadata.catalog?.names, `Map ${m.game_map_id}`)} ·{" "}
                {m.game_version}{" "}
                {m.layer === "gravity:2" ? " · Inverted" : ""}{" "}
              </option>
            ))}{" "}
          </select>{" "}
        </label>{" "}
        <div className="atlas-menu-toolbar">
          {" "}
          <label>
            {" "}
            Location{" "}
            <select
              value={area}
              onChange={(e) =>
                change("area", e.target.value === "0" ? "" : e.target.value)
              }
            >
              {" "}
              <option value="0">All locations</option>{" "}
              {[
                ...new Set(
                  locations.map((l) => name(l.group_names, "Other locations")),
                ),
              ].map((group) => (
                <optgroup key={group} label={group}>
                  {" "}
                  {locations
                    .filter(
                      (l) => name(l.group_names, "Other locations") === group,
                    )
                    .map((l) => (
                      <option key={l.id} value={l.id}>
                        {" "}
                        {name(l.names, `Location ${l.id}`)}{" "}
                      </option>
                    ))}{" "}
                </optgroup>
              ))}{" "}
            </select>{" "}
          </label>{" "}
          <label>
            {" "}
            Find an object{" "}
            <input
              type="search"
              placeholder="Plant, chest, beacon…"
              value={search}
              onChange={(e) => {
                change("q", e.target.value);
                setListLimit(60);
              }}
            />{" "}
          </label>{" "}
        </div>{" "}
        <details className="atlas-levels" open={Boolean(active)}>
          {" "}
          <summary>Layers & display</summary>{" "}
          <label className="atlas-check">
            {" "}
            <input
              type="checkbox"
              checked={includeHidden}
              onChange={(e) => change("hidden", e.target.checked ? "1" : "")}
            />{" "}
            Include hidden game placements{" "}
          </label>{" "}
          <label>
            {" "}
            Visible floor{" "}
            <div
              className="atlas-floor-buttons"
              role="group"
              aria-label="Map floors"
            >
              {" "}
              <button
                aria-pressed={!active}
                onClick={() => change("floor", "")}
              >
                {" "}
                Surface{" "}
              </button>{" "}
              {[
                ...new Set(
                  floorOptions.map(
                    (m) =>
                      base?.metadata.catalog?.floors?.[m.layer.split(":")[1]]
                        ?.group_id,
                  ),
                ),
              ].map((group) => (
                <details
                  key={group}
                  className="atlas-floor-group"
                  open={floorOptions.some(
                    (m) =>
                      m.layer === active &&
                      base?.metadata.catalog?.floors?.[m.layer.split(":")[1]]
                        ?.group_id === group,
                  )}
                >
                  {" "}
                  <summary>
                    {" "}
                    {name(
                      base?.metadata.catalog?.floors?.[
                        floorOptions
                          .find(
                            (m) =>
                              base?.metadata.catalog?.floors?.[
                                m.layer.split(":")[1]
                              ]?.group_id === group,
                          )
                          ?.layer.split(":")[1] ?? ""
                      ]?.group_names,
                      `Area ${group}`,
                    )}{" "}
                  </summary>{" "}
                  {floorOptions
                    .filter(
                      (m) =>
                        base?.metadata.catalog?.floors?.[m.layer.split(":")[1]]
                          ?.group_id === group,
                    )
                    .map((m) => (
                      <button
                        key={m.id}
                        aria-pressed={active === m.layer}
                        onClick={() => change("floor", m.layer)}
                      >{`Floor ${base?.metadata.catalog?.floors?.[m.layer.split(":")[1]]?.floor ?? m.layer.split(":")[1]}`}</button>
                    ))}{" "}
                </details>
              ))}{" "}
            </div>{" "}
          </label>{" "}
          {active && (
            <>
              {" "}
              <label>
                {" "}
                Surface opacity <output>{opacity}%</output>{" "}
                <input
                  type="range"
                  min="0"
                  max="100"
                  value={opacity}
                  onChange={(e) => change("opacity", e.target.value)}
                />{" "}
              </label>{" "}
              <label className="atlas-check">
                {" "}
                <input
                  type="checkbox"
                  checked={unknown}
                  onChange={(e) =>
                    change("unknown", e.target.checked ? "" : "0")
                  }
                />{" "}
                Include objects with unknown floor{" "}
              </label>{" "}
            </>
          )}{" "}
          <small>
            {" "}
            Floor placement is shown only where the game records it.{" "}
          </small>{" "}
        </details>{" "}
        {search && (
          <div className="atlas-search-result">
            {" "}
            <span>
              {" "}
              {
                types.filter(([, item]) =>
                  `${objectName(item.marker)} ${item.marker.metadata.names?.en ?? ""} ${categories[item.marker.category]?.[0]} ${item.marker.blueprint_type}`
                    .toLowerCase()
                    .includes(search.toLowerCase()),
                ).length
              }{" "}
              matching types{" "}
            </span>{" "}
            <button onClick={() => change("q", "")}>Clear search</button>{" "}
          </div>
        )}{" "}
        <div className="atlas-global-controls" aria-label="All map markers">
          {" "}
          <span>Map markers</span>{" "}
          <div>
            {" "}
            <button disabled={loading} onClick={() => change("hide", "")}>
              {" "}
              Show all{" "}
            </button>{" "}
            <button
              disabled={loading}
              onClick={() => {
                change(
                  "hide",
                  [
                    ...new Set([
                      ...Object.keys(categories),
                      ...markers.map((marker) => marker.category),
                    ]),
                  ].join(","),
                );
                setSelected(null);
                setCluster([]);
              }}
            >
              {" "}
              Hide all{" "}
            </button>{" "}
          </div>{" "}
          <button
            onClick={() =>
              setCollapsedGroups(new Set(menuGroups.map((group) => group.key)))
            }
          >
            {" "}
            Collapse groups{" "}
          </button>{" "}
        </div>{" "}
        {menuGroups.map((group) => {
          const entries = types.filter(
            ([, item]) =>
              group.categories.includes(item.marker.category) &&
              (group.key === "unidentified"
                ? !item.marker.metadata.names?.en
                : group.key !== "featured" ||
                  item.marker.category !== "collectible" ||
                  Boolean(item.marker.metadata.names?.en)) &&
              (item.marker.category !== "resource" ||
                (item.marker.metadata.resource_group ?? "gathering") ===
                  group.key) &&
              (!search ||
                `${objectName(item.marker)} ${item.marker.metadata.names?.en ?? ""} ${categories[item.marker.category]?.[0]} ${item.marker.blueprint_type}`
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
            change("hide", [...excluded].join(","));
          };
          return (
            <section className="atlas-menu-group" key={group.key}>
              {" "}
              <header>
                {" "}
                <h2>
                  {" "}
                  <button
                    className="atlas-group-toggle"
                    aria-expanded={!collapsedGroups.has(group.key)}
                    aria-controls={`atlas-group-${group.key}`}
                    onClick={() => toggleGroup(group.key)}
                  >
                    {" "}
                    <span aria-hidden="true">
                      {" "}
                      {collapsedGroups.has(group.key) ? "+" : "−"}{" "}
                    </span>{" "}
                    {group.title}{" "}
                  </button>{" "}
                </h2>{" "}
                <div>
                  {" "}
                  <button
                    onClick={selectAll}
                    aria-label={`Select all ${group.title}`}
                  >
                    {" "}
                    Select all{" "}
                  </button>{" "}
                  <button
                    onClick={() =>
                      change(
                        "hide",
                        [
                          ...new Set([...disabled.filter(Boolean), ...keys]),
                        ].join(","),
                      )
                    }
                    aria-label={`Clear ${group.title}`}
                  >
                    {" "}
                    Clear{" "}
                  </button>{" "}
                </div>{" "}
              </header>{" "}
              <div
                id={`atlas-group-${group.key}`}
                hidden={collapsedGroups.has(group.key)}
                className={
                  group.key === "gathering"
                    ? "atlas-type-grid compact"
                    : "atlas-type-grid"
                }
              >
                {" "}
                {entries.map(([type, item]) => {
                  const enabled =
                    !disabled.includes(item.marker.category) &&
                    !disabled.includes(type);
                  const title =
                    item.marker.category === "combat_activity"
                      ? "Dream Patrol"
                      : objectName(item.marker);
                  return (
                    <button
                      key={type}
                      className={
                        enabled ? "atlas-type-tile enabled" : "atlas-type-tile"
                      }
                      aria-pressed={enabled}
                      aria-label={`${title}, ${item.count} locations`}
                      title={`${title} · ${item.count} locations`}
                      onClick={() => toggleType(type, item.marker)}
                      onFocus={() => setLastType(title)}
                    >
                      {" "}
                      <ObjectIcon
                        marker={item.marker}
                        icons={base?.icons}
                      />{" "}
                      <span className="atlas-tile-name">{title}</span>{" "}
                      <span className="atlas-tile-count">
                        {" "}
                        {item.count.toLocaleString()}{" "}
                      </span>{" "}
                    </button>
                  );
                })}{" "}
              </div>{" "}
            </section>
          );
        })}{" "}
        {lastType && (
          <p className="atlas-last-type" role="status">
            {" "}
            {lastType}{" "}
          </p>
        )}{" "}
        <details className="atlas-object-list">
          {" "}
          <summary>
            {" "}
            In this view ·{" "}
            {filtered
              .filter((m) => visible.has(m.id))
              .length.toLocaleString()}{" "}
          </summary>{" "}
          {filtered
            .filter((m) => visible.has(m.id))
            .slice(0, listLimit)
            .map((m) => (
              <button
                key={m.id}
                className={selected?.id === m.id ? "selected" : ""}
                onClick={() => {
                  setSelected(m);
                  setFocus(m);
                }}
              >
                {" "}
                <ObjectIcon marker={m} icons={base?.icons} />{" "}
                {objectName(m)}{" "}
              </button>
            ))}{" "}
          {filtered.filter((m) => visible.has(m.id)).length > listLimit && (
            <button onClick={() => setListLimit((n) => n + 60)}>
              {" "}
              Show more objects{" "}
            </button>
          )}{" "}
          {!loading && !filtered.length && (
            <p>No objects match. Clear the search or show all categories.</p>
          )}{" "}
        </details>{" "}
      </aside>{" "}
      <div className="atlas-map-area">
        {" "}
        <nav className="atlas-region-rail" aria-label="Switch map region">
          {" "}
          {roots.map((m) => {
            const title =
              name(m.metadata.catalog?.names, `Map ${m.game_map_id}`) +
              (m.layer === "gravity:2" ? " · Inverted" : "");
            return (
              <button
                key={m.id}
                aria-label={title}
                title={title}
                aria-pressed={chosen?.id === m.id}
                onClick={() => {
                  setParams({ map: String(m.id) });
                  setFocus(null);
                  setSelected(null);
                  setCluster([]);
                }}
              >
                {" "}
                <span>
                  {" "}
                  {title
                    .split(/\s+/)
                    .map((word) => word[0])
                    .join("")
                    .slice(0, 3)}{" "}
                </span>{" "}
                <span className="atlas-region-name">{title}</span>{" "}
              </button>
            );
          })}{" "}
        </nav>{" "}
        {!loading && !maps.length && !error && (
          <div className="atlas-error" role="status">
            {" "}
            No maps have been imported yet.{" "}
          </div>
        )}{" "}
        {base && (
          <MapCanvas
            base={base}
            floors={floors}
            active={active}
            opacity={opacity}
            markers={filtered}
            selected={selected}
            onSelect={setSelected}
            focus={focus}
            onVisible={setVisible}
            onCluster={setCluster}
          />
        )}{" "}
        <div className="atlas-map-status" role="status">
          {" "}
          {loading
            ? "Loading map objects…"
            : `${filtered.length.toLocaleString()} objects · ${chosen?.game_version ?? ""}`}{" "}
          <span>Scroll to zoom · drag to explore</span>{" "}
        </div>{" "}
        {error && (
          <div className="atlas-error" role="alert">
            {" "}
            <p>{error}</p>{" "}
            <button onClick={() => setRetry((n) => n + 1)}>Retry</button>{" "}
          </div>
        )}{" "}
        {cluster.length > 0 && (
          <article className="atlas-detail atlas-cluster-detail">
            {" "}
            <button
              className="atlas-close"
              aria-label="Close nearby objects"
              onClick={() => setCluster([])}
            >
              {" "}
              ×{" "}
            </button>{" "}
            <h2>{cluster.length} objects here</h2>{" "}
            {cluster.map((m) => (
              <button
                key={m.id}
                onClick={() => {
                  setSelected(m);
                  setCluster([]);
                  setFocus(m);
                }}
              >
                {" "}
                <ObjectIcon marker={m} icons={base?.icons} />{" "}
                {objectName(m)}{" "}
              </button>
            ))}{" "}
          </article>
        )}{" "}
        {selected && cluster.length === 0 && (
          <article className="atlas-detail">
            {" "}
            <button
              className="atlas-close"
              aria-label="Close object details"
              onClick={() => setSelected(null)}
            >
              {" "}
              ×{" "}
            </button>{" "}
            <span className="eyebrow">
              {" "}
              {categories[selected.category]?.[0] ?? "Map object"}{" "}
            </span>{" "}
            <h2>{objectName(selected)}</h2>{" "}
            {name(selected.metadata.description) && (
              <p>{name(selected.metadata.description)}</p>
            )}{" "}
            <p>
              {" "}
              {selected.metadata.floor
                ? `Recorded floor: ${selected.metadata.floor}`
                : "Floor not recorded"}{" "}
            </p>{" "}
            {Boolean(selected.metadata.condition_id) && (
              <p>This mark appears under an in-game condition.</p>
            )}{" "}
            <small>
              {" "}
              Game placements may depend on progress or respawn state.{" "}
            </small>{" "}
            <details>
              {" "}
              <summary>Position & source</summary>{" "}
              <p>
                {" "}
                X {selected.world[0].toFixed(0)} · Y{" "}
                {selected.world[1].toFixed(0)} · Z{" "}
                {selected.world[2].toFixed(0)}{" "}
              </p>{" "}
              <p>
                {" "}
                {selected.blueprint_type} · {selected.entity_id}{" "}
              </p>{" "}
            </details>{" "}
          </article>
        )}{" "}
      </div>{" "}
    </section>
  );
}
