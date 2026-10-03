export type SourceCitation = {
  node_id: number;
  quote: string;
  locale?: string | null;
  href?: string | null;
};
export type ExplanationBlock = {
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
  status: 'confirmed' | 'observed_anomaly' | 'inferred' | 'unresolved';
  citations: SourceCitation[];
  chronology_in_quest: { order: number; anchor_node_id: number; label: string };
  world_chronology: {
    placement: 'before_quest' | 'during_quest' | 'after_quest' | 'unknown';
    explanation: string;
  };
  knowledge_state: string;
  later_resolution: {
    status: 'resolved' | 'partial' | 'contradicted';
    text: string;
    revealed_in_node_id: number;
    citations: SourceCitation[];
  }[];
};
export type StoryExplanation = {
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
