import type { StoryMap } from '@/data/story';

export const STORY_PAGE_SIZE = 60;
type Chapter = StoryMap['chapters'][number];
export type StoryChapterGroup = {
  index: number;
  title: string;
  subtitle: string | null;
  unchaptered: boolean;
  version: string;
  treeAvailable: boolean;
  acts: Chapter[];
  count: number;
};

export function storyPageNumber(value: unknown): number {
  const page = Number(value);
  return /^\d+$/.test(String(value)) && Number.isSafeInteger(page) && page > 0 ? page : 1;
}

/** Assign chapter identities before filtering or paging, in the authored order. */
export function storyChapterGroups(chapters: Chapter[]): StoryChapterGroup[] {
  const groups: StoryChapterGroup[] = [];
  for (const chapter of chapters) {
    if (!chapter.nodes.length) continue;
    const title = chapter.title || `Chapter ${chapter.id}`;
    const subtitle = chapter.act_title === title ? null : chapter.chapter_number || null;
    const last = groups.at(-1);
    if (last && !chapter.tree_available && last.title === title && last.subtitle === subtitle &&
        last.unchaptered === (chapter.id === 0) && last.version === chapter.snapshot_game_version) {
      last.acts.push(chapter);
      last.count += chapter.nodes.length;
    } else {
      groups.push({ index: groups.length, title, subtitle, unchaptered: chapter.id === 0,
        version: chapter.snapshot_game_version, treeAvailable: chapter.tree_available,
        acts: [chapter], count: chapter.nodes.length });
    }
  }
  return groups;
}

/** Search the entire selection, then bound the mounted source nodes across acts. */
export function storyMapPage(groups: StoryChapterGroup[], query: string, requestedPage: unknown) {
  const needle = query.trim().toLocaleLowerCase();
  const matches = groups.map((group) => {
    const acts = group.acts.map((act) => ({ ...act, nodes: act.nodes.filter((node) => !needle ||
      [act.title, act.act_title, act.snapshot_game_version, node.title, node.chapter_label,
        ...node.quests.map((quest) => quest.title)]
        .some((value) => value?.toLocaleLowerCase().includes(needle))) }))
      .filter((act) => act.nodes.length);
    return { ...group, acts, count: acts.reduce((sum, act) => sum + act.nodes.length, 0) };
  }).filter((group) => group.count);
  const total = matches.reduce((sum, group) => sum + group.count, 0);
  const pages = Math.max(1, Math.ceil(total / STORY_PAGE_SIZE));
  const page = Math.min(storyPageNumber(requestedPage), pages);
  const offset = (page - 1) * STORY_PAGE_SIZE;
  let position = 0;
  const visibleGroups = matches.flatMap((group) => {
    const acts = group.acts.flatMap((act) => {
      const start = Math.max(0, offset - position);
      const end = Math.min(act.nodes.length, offset + STORY_PAGE_SIZE - position);
      position += act.nodes.length;
      return end > start ? [{ ...act, nodes: act.nodes.slice(start, end), start: start + 1 }] : [];
    });
    return acts.length ? [{ ...group, acts }] : [];
  });
  return { matches, visibleGroups, total, pages, page, start: total ? offset + 1 : 0,
    end: Math.min(offset + STORY_PAGE_SIZE, total) };
}

/** Existing chapter hashes point to the first page containing that chapter. */
export function storyPageForHash(groups: StoryChapterGroup[], hash: string): number | null {
  let decoded: string;
  try { decoded = decodeURIComponent(hash); } catch { return null; }
  const match = /^#story-chapter-group-(\d+)$/.exec(decoded);
  if (!match) return null;
  const index = Number(match[1]);
  let offset = 0;
  for (const group of groups) {
    if (group.index === index) return Math.floor(offset / STORY_PAGE_SIZE) + 1;
    offset += group.count;
  }
  return null;
}
