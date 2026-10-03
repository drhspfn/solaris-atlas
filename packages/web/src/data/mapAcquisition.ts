type SourceMarker = {
  id: number;
  category: string;
  blueprint_type: string;
  metadata: {
    item_id?: number;
    drop_item_ids?: number[];
    names?: Record<string, string>;
    icon_source?: string;
    type_key?: string;
  };
};

export const markerType = (m: SourceMarker) =>
  `${m.category}:${m.metadata.item_id ? `item:${m.metadata.item_id}` : m.category === 'combat_activity' ? 'dream-patrol' : m.metadata.names?.en || m.metadata.icon_source || m.metadata.type_key || m.blueprint_type}`;

export function acquisitionMarkers<T extends SourceMarker>(
  markers: T[],
  itemId: number,
  sourceId: number,
): T[] {
  const source = markers.find((m) => m.id === sourceId);
  if (!source || !Number.isSafeInteger(itemId) || itemId <= 0) return [];
  return markers.filter(
    (m) =>
      markerType(m) === markerType(source) &&
      (m.metadata.item_id === itemId || m.metadata.drop_item_ids?.includes(itemId)),
  );
}
