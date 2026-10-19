/** Convert a URL segment to the canonical ID before passing it to API clients. */
export function decodeRouteKey(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    // Keep malformed escapes as data; encodeURIComponent still handles them
    // safely when building the API URL.
    return value;
  }
}

export type PublicAddress = {
  slug?: string | null;
  href?: string;
  canonical_key?: string;
  game_quest_id?: string | number;
  quest_id?: string | number;
  quest?: string;
  quest_slug?: string | null;
  quest_href?: string;
};

export function publicPath(group: string, entity: PublicAddress, fallback = ''): string {
  if (entity.href?.startsWith(`/${group}/`)) return entity.href;
  const key =
    entity.slug ||
    (group === 'quests'
      ? (entity.game_quest_id ?? entity.canonical_key?.split(':').at(-1) ?? fallback)
      : entity.canonical_key || fallback);
  return `/${group}/${encodeURIComponent(key)}`;
}

export function questPath(quest: PublicAddress | string | number): string {
  if (typeof quest !== 'object')
    return publicPath('quests', {}, String(quest).replace(/^quest:/, ''));
  if (quest.quest_href?.startsWith('/quests/')) return quest.quest_href;
  return publicPath(
    'quests',
    {
      ...quest,
      canonical_key: quest.canonical_key || quest.quest,
      slug: quest.slug || quest.quest_slug,
    },
    String(quest.quest_id ?? ''),
  );
}
