export type ReaderLine = {
  id: string;
  flow_state?: string;
  illustration?: { id: string; kind: string };
  player_choices?: Array<{
    id: string;
    text?: unknown;
    branch?: {
      line_ids: string[];
      continuation_line_id?: string | null;
    };
  }>;
};

export type QuestForkData = {
  source: string;
  paths: Array<{
    id: string;
    text?: unknown;
    lineIds: string[];
    continuation: string | null;
  }>;
  continuation: string | null;
};

// Only complete, disjoint authored branches can leave the main transcript.
// Ambiguous/cyclic/partial-page branches retain their original presentation.
export function questForks(lines: ReaderLine[]) {
  const available = new Set(lines.map((line) => line.id));
  const candidates: QuestForkData[] = [];
  for (const line of lines) {
    const choices = line.player_choices || [];
    if (
      choices.length < 2 ||
      choices.some(
        (choice) =>
          !choice.branch ||
          choice.branch.line_ids.some((id) => !available.has(id) || id === line.id),
      )
    )
      continue;
    const paths = choices.map((choice) => ({
      id: choice.id,
      text: choice.text,
      lineIds: choice.branch!.line_ids,
      continuation: choice.branch!.continuation_line_id || null,
    }));
    const ids = paths.flatMap((path) => path.lineIds);
    if (new Set(ids).size !== ids.length) continue;
    const continuations = new Set(paths.map((path) => path.continuation));
    const continuation = continuations.size === 1 ? paths[0].continuation : null;
    if (paths.some((path) => path.continuation && ids.includes(path.continuation))) continue;
    candidates.push({ source: line.id, paths, continuation });
  }
  // Nested forks are left in the ordinary transcript until a complete branch
  // hierarchy is available; never hide a source choice inside another fork.
  const sources = new Set(candidates.map((fork) => fork.source));
  const owners = new Map<string, number>();
  for (const fork of candidates)
    for (const path of fork.paths)
      for (const id of path.lineIds) owners.set(id, (owners.get(id) || 0) + 1);
  return candidates.filter(
    (fork) =>
      !fork.paths.some((path) => path.lineIds.some((id) => sources.has(id))) &&
      !fork.paths.some((path) => path.lineIds.some((id) => owners.get(id)! > 1)) &&
      !candidates.some(
        (other) => other !== fork && other.paths.some((path) => path.lineIds.includes(fork.source)),
      ),
  );
}

export function illustratedRuns(lines: ReaderLine[], breakBefore = new Set<string>()) {
  const runs: ReaderLine[][] = [];
  let last = -2;
  for (const [index, line] of lines.entries()) {
    if (!line.illustration || line.illustration.kind === 'clear' || line.player_choices?.length)
      continue;
    const previous = runs.at(-1);
    if (
      previous &&
      !breakBefore.has(line.id) &&
      last + 1 === index &&
      previous[0].flow_state === line.flow_state
    )
      previous.push(line);
    else runs.push([line]);
    last = index;
  }
  return runs;
}
