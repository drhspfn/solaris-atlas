import type { QuestMediaEvent } from "./QuestMediaReferences";

export type EncounterPhase =
  | "EnterActions"
  | "Actions"
  | "Condition"
  | "FinishActions"
  | "SaveConfig.EnterActions"
  | "SaveConfig.Actions"
  | "SaveConfig.FinishActions";
export type EncounterSource = {
  file?: string;
  key?: string;
  raw_path?: string;
  [key: string]: unknown;
};
export type EncounterCinematic = {
  kind: "sequence" | "cutscene";
  reference: string;
  raw_path: string;
  basis: string;
  sequence_names?: string[];
  renderable: false;
  playback_status: "unresolved";
  async?: boolean | null;
  boundary_semantics: "authored_action_extent";
  phase?: EncounterPhase;
  execution_context?: "authored" | "save_config";
  start: { action_index: number; anchor: "before_action" };
  end: { action_index: number; anchor: "after_action" };
  source: EncounterSource;
};
export type SequenceMedia = {
  schema_version: 1; complete_video: false; game_source?: unknown;
  sections: { kind: string; source?: unknown; reference?: string; status?: string; url?: string | null; file_id?: number | null; [key: string]: unknown }[];
};
export type EncounterFlow = {
  id: string;
  state: string;
  state_node_id: number | null;
  phase: EncounterPhase;
  execution_context?: "authored" | "save_config";
  async: boolean | null;
  timing: "during" | "blocking" | "unknown";
  sequence_media?: SequenceMedia;
  cinematics: EncounterCinematic[];
  source: EncounterSource;
};
export type EncounterNode = {
  id: string;
  owner: string;
  node_type: string;
  path: number[];
  children_order:
    | "sequence"
    | "parallel"
    | "conditional"
    | "alternative"
    | "decorator"
    | "unknown";
  title: string;
  text_key?: string | null;
  condition?: { Type?: string; [key: string]: unknown } | null;
  count?: number | null;
  branch_condition?: unknown;
  children: EncounterNode[];
  flow_states: EncounterFlow[];
  media: EncounterCinematic[];
  completions: {
    is_success: boolean;
    phase: EncounterPhase;
    execution_context?: "authored" | "save_config";
    source: EncounterSource;
  }[];
  action_sections?: {
    phase: EncounterPhase;
    execution_context: "authored" | "save_config";
    runtime_order: "authored_array" | "unknown";
    status: "decoded" | "unsupported_shape";
    unprojected_actions: { index: number; name: string | null }[];
    source: EncounterSource;
  }[];
  source: EncounterSource;
};
export type Encounter = {
  id: string;
  kind: "encounter";
  dungeon_id: number;
  title: string;
  release_id: number;
  client?: { asset_version?: string; download_id?: string };
  status: "verified" | "partial" | "unresolved";
  trees_order: "unknown";
  source: EncounterSource;
  trees: {
    level_play_id: number;
    ordering: "authored_tree";
    membership: EncounterSource;
    root: EncounterNode;
  }[];
  unresolved_level_plays?: {
    level_play_id: number;
    membership: EncounterSource;
    reason: string;
  }[];
};

export function encounterFlows(encounter: Encounter): EncounterFlow[] {
  const visit = (node: EncounterNode): EncounterFlow[] => [
    ...node.flow_states,
    ...node.children.flatMap(visit),
  ];
  return encounter.trees.flatMap((tree) => visit(tree.root));
}

// Ownership is presentation-only: repeated occurrences remain in the tree.
// Mapped quest lines keep their existing global location.
export function encounterFlowOwners(
  blocks: { encounter?: Encounter }[],
  lines: { flow_state: string; story_position?: number[] }[],
): Map<string, string> {
  const available = new Set(
    lines.filter((line) => !line.story_position).map((line) => line.flow_state),
  );
  const owners = new Map<string, string>();
  for (const block of blocks) {
    if (!block.encounter) continue;
    for (const flow of encounterFlows(block.encounter)) {
      if (
        flow.execution_context !== "save_config" &&
        !flow.phase.startsWith("SaveConfig.") &&
        available.has(flow.state) &&
        !owners.has(flow.state)
      )
        owners.set(flow.state, flow.id);
    }
  }
  return owners;
}

export function encounterOwnsMedia(
  event: QuestMediaEvent,
  owners: Map<string, string>,
  blocks: { encounter?: Encounter }[],
): boolean {
  return blocks.some(
    (block) =>
      block.encounter &&
      encounterFlows(block.encounter).some(
        (flow) =>
          owners.get(flow.state) === flow.id &&
          (event.flow_state === flow.state ||
            event.transcript_states?.includes(flow.state)) &&
          flow.cinematics.some((scene) => scene.reference === event.reference),
      ),
  );
}

export function encounterAnchor(id: string): string {
  return `encounter-${Array.from(id, (char) => char.codePointAt(0)!.toString(16)).join("-")}`;
}

// Group only action-index matches inside one flow; never bind a whole flow to a movie.
export function encounterCinematicParts<
  T extends { action?: { index: number } | null },
>(
  lines: T[],
  cinematics: EncounterCinematic[],
): { lines: T[]; cinematics: EncounterCinematic[] }[] {
  const parts: { lines: T[]; cinematics: EncounterCinematic[] }[] = [];
  const used = new Set<EncounterCinematic>();
  for (const line of lines) {
    const action = line.action?.index;
    const matched =
      action === undefined
        ? []
        : cinematics.filter(
            (scene) => scene.start.action_index === action && !used.has(scene),
          );
    const previous = parts.at(-1);
    if (matched.length) {
      matched.forEach((scene) => used.add(scene));
      parts.push({ lines: [line], cinematics: matched });
    } else if (
      previous &&
      (!previous.cinematics.length ||
        previous.cinematics[0].start.action_index === action)
    ) {
      previous.lines.push(line);
    } else {
      parts.push({ lines: [line], cinematics: [] });
    }
  }
  for (const scene of cinematics)
    if (!used.has(scene)) parts.push({ lines: [], cinematics: [scene] });
  return parts;
}
