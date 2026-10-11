export type LocalizedText = { content?: string | null } | null;

export type StoryQuest = {
  game_quest_id: number;
  title: string;
  first_observed_game_version: string | null;
  source: { file: string; raw_path: string } | null;
};

export type StoryTreeNode = {
  id: number;
  title: string;
  chapter_label: string | null;
  quest_type_id: number;
  node_type_id: number | null;
  source_kind: 'quest_tree' | 'quest_prerequisite';
  previous_node_ids: number[];
  next_node_id: number | null;
  quests: StoryQuest[];
  source: { file: string; raw_path: string };
};

export type StoryMap = {
  selected_game_version: string | null;
  imported_game_versions: string[];
  quest_type_id: number;
  tree_available: boolean;
  chapters: {
    id: number;
    title: string | null;
    chapter_number?: string | null;
    act_title?: string | null;
    act_number?: string | null;
    snapshot_game_version: string;
    tree_available: boolean;
    nodes: StoryTreeNode[];
  }[];
  ordering_basis: string;
  version_basis: string;
};

export type QuestContinuity = {
  quest: string;
  selected_game_version: string;
  present_in_selected_snapshot: boolean;
  first_observed_game_version: string | null;
  quest_type: { id: number | null; name: LocalizedText };
  quest_chapter: { id: number | null; chapter_name: LocalizedText; act_name: LocalizedText };
  quest_tree_nodes: {
    canonical_key: string;
    name: LocalizedText;
    tree_chapter_name: LocalizedText;
    neighbors: {
      relation: 'quest_tree_predecessor' | 'quest_tree_next';
      canonical_key: string;
      quest_ids: number[];
      name: LocalizedText;
      source: { file: string; raw_path: string };
    }[];
  }[];
  prerequisites: {
    quest: string;
    name: LocalizedText;
    source: { file: string; raw_path: string };
  }[];
  required_by: { quest: string; name: LocalizedText; source: { file: string; raw_path: string } }[];
};
/** Keep source identity intact, including non-Latin keys and URL punctuation. */
export function questPassagePath(
  questId: string | number,
  line: string,
  locale: string,
  version?: string,
) {
  const params = new URLSearchParams({ line, locale });
  if (version) params.set('game_version', version);
  return `/quests/${encodeURIComponent(questId)}?${params}`;
}
