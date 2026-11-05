export type SidebarEntry = {
  id: string;
  title: unknown;
  fallback: string;
  searchText: string;
  href?: string;
  note?: string;
  inView: boolean;
};

export function dialogueSearchIndex(lines: Array<{ flow_state?: string; text: string }>) {
  const index = new Map<string, string>();
  for (const line of lines) {
    const state = line.flow_state || 'Unassigned';
    index.set(state, `${index.get(state) || ''} ${line.text}`.trim());
  }
  return index;
}

export function sidebarWindow(entries: SidebarEntry[], query: string, limit = 10, activeHref = '') {
  const search = query.trim().toLocaleLowerCase();
  const matches = entries.filter((entry) => entry.searchText.toLocaleLowerCase().includes(search));
  const active = !search && activeHref ? matches.findIndex((entry) => entry.href === activeHref) : -1;
  const end = Math.min(matches.length, Math.max(limit, active + 1));
  const start = Math.max(0, end - limit);
  return { entries: matches.slice(start, end), total: matches.length, start };
}

type Activity = {
  id: string;
  flow_states?: Array<{ state: string }>;
  interactions?: Array<{ flow_states: Array<{ state: string }> }>;
  encounter?: { trees: Array<{ root: EncounterNode }> };
};
type EncounterNode = { flow_states?: Array<{ state: string }>; children?: EncounterNode[] };

export function activityDestination(
  block: Activity,
  mountedAnchors: ReadonlyMap<string, string>,
  lines: Array<{ id: string; flow_state?: string }>,
  search: string,
) {
  const anchor = mountedAnchors.get(block.id);
  if (anchor) return `#${anchor}`;
  const states = new Set((block.flow_states || []).map((flow) => flow.state));
  for (const interaction of block.interactions || [])
    for (const flow of interaction.flow_states) states.add(flow.state);
  const visit = (node: EncounterNode) => {
    for (const flow of node.flow_states || []) states.add(flow.state);
    for (const child of node.children || []) visit(child);
  };
  for (const tree of block.encounter?.trees || []) visit(tree.root);
  const line = lines.find((entry) => entry.flow_state && states.has(entry.flow_state));
  if (!line) return undefined;
  const params = new URLSearchParams(search);
  params.set('line', line.id);
  return `?${params.toString()}`;
}
