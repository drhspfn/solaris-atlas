export type SourceCitation = { node_id: number; quote: string };
export type ExplanationBlock = {
  title: string;
  text: string;
  kind: 'summary' | 'explanation' | 'annotation';
  citations: SourceCitation[];
  related_node_ids: number[];
};
export type StoryExplanation = {
  id: number;
  quest_id: number;
  game_version: string;
  title: string;
  blocks: ExplanationBlock[];
  unresolved_questions: string[];
  nodes: { id: number; canonical_key: string; kind: string; label: string; href: string }[];
  events: { node_id: number; title: string }[];
  links: { from_node_id: number; to_node_id: number; relation: string; explanation: string }[];
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
