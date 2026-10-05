export type SourceCitation = {
  snapshot_id?: number | null;
  game_version?: string | null;
  node_id: number;
  quote: string;
  locale?: string | null;
  href?: string | null;
};
export type ExplanationBlock = {
  scene_importance?: string;
  title: string;
  text: string;
  kind: 'summary' | 'explanation' | 'annotation';
  citations: SourceCitation[];
  related_node_ids: number[];
  related_records?: { node_id: number; label: string }[];
  assertions?: StoryAssertion[];
};
export type StoryAssertion = {
  text: string;
  status:
    | 'confirmed'
    | 'observed_anomaly'
    | 'inferred'
    | 'unresolved'
    | 'suggested'
    | 'character_speculation'
    | 'theory';
  occurrence?: 'mandatory' | 'player_choice' | 'conditional' | 'optional' | 'unknown';
  condition?: string | null;
  citations: SourceCitation[];
  chronology_in_quest: { order: number; anchor_node_id: number; label: string };
  world_chronology: {
    placement: 'before_quest' | 'during_quest' | 'after_quest' | 'unknown';
    explanation: string;
  };
  knowledge_state: string;
  later_resolution: {
    status: 'resolved' | 'partial' | 'contradicted' | 'suggested';
    text: string;
    revealed_in_node_id: number;
    citations: SourceCitation[];
  }[];
};
export type StoryExplanation = {
  is_supplement?: boolean;
  assessment?: {
    narrative_weight: string;
    secondary_functions?: string[];
    hook_priority: string;
    reason: string;
  } | null;
  narrative_function?: string | null;
  knowledge_boundary?: { known: string[]; unknown: string[]; cannot_conclude: string[] } | null;
  loaded_versions?: string[];
  corpus_changed?: boolean;
  hooks?: {
    key: string;
    question: string;
    priority: string;
    revisit_on_new_versions: boolean;
    revisit_reason: string;
    citations: SourceCitation[];
  }[];
  revisited_hooks?: {
    hook_key: string;
    priority: string;
    status: string;
    explanation: string;
    citations: SourceCitation[];
  }[];
  supplements?: StoryExplanation[];
  id: number;
  quest_id: number;
  game_version: string;
  locale?: string;
  requested_locale?: string;
  title: string;
  blocks: ExplanationBlock[];
  unresolved_questions: string[];
  nodes: { id: number; canonical_key: string; kind: string; label: string; href: string }[];
  events: { node_id: number; title: string }[];
  links: {
    from_node_id: number;
    to_node_id: number;
    relation: string;
    relation_label?: string | null;
    explanation: string;
    confidence?: number;
    certainty?: 'confirmed' | 'suggested' | 'inferred' | 'theory';
    citations?: SourceCitation[];
  }[];
};
export type ExplanationResponse = {
  status: 'available' | 'pending';
  explanation: StoryExplanation | null;
};
export type ExplanationResults = {
  results: (StoryExplanation & { score: number })[];
  mode: 'text' | 'vector';
  game_version: string;
};
