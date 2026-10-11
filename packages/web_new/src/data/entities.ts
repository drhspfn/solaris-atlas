import { BookOpen, Flower2, MapPin, Users } from 'lucide-react';

export type Category = 'character' | 'item' | 'location' | 'quest' | 'speaker' | 'dialogue';
export type Entity = {
  id: number;
  canonical_key: string;
  node_type: string;
  category?: Category;
  alias?: string;
  title?: string;
  slug?: string;
  score?: number;
  image_url?: string | null;
};
export const categories = [
  {
    key: 'character',
    label: 'Characters',
    icon: Users,
    description: 'Resonators, voices & story connections',
  },
  {
    key: 'quest',
    label: 'Quests',
    icon: BookOpen,
    description: 'Main story, companion quests & more',
  },
  {
    key: 'location',
    label: 'Locations',
    icon: MapPin,
    description: 'Regions, areas & places in Solaris-3',
  },
  { key: 'item', label: 'Items', icon: Flower2, description: 'Objects, materials & lore' },
] satisfies { key: Category; label: string; icon: typeof Users; description: string }[];
export const categoryPath: Record<string, string> = {
  character: 'characters',
  item: 'items',
  location: 'locations',
  quest: 'quests',
  speaker: 'speakers',
  dialogue: 'dialogue',
};
export const categoryTitle: Record<string, string> = {
  ...Object.fromEntries(categories.map((c) => [c.key, c.label])),
  speaker: 'Speakers',
  dialogue: 'Dialogue',
};
export function entityPath(item: Entity) {
  if (item.node_type === 'quest' || item.category === 'quest')
    return `/quests/${item.canonical_key.split(':').at(-1)}`;
  const category =
    item.category ?? (item.node_type === 'area' ? 'location' : (item.node_type as Category));
  const path = categoryPath[category];
  if (!path || path === 'speakers' || path === 'dialogue') {
    return `/nodes/${encodeURIComponent(item.canonical_key)}`;
  }
  return `/${path}/${encodeURIComponent(item.canonical_key)}`;
}
export function display(item: Entity) {
  return item.title || item.alias || item.slug || item.canonical_key;
}
