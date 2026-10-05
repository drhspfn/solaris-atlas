import { preferredRoverTarget } from './preferredRover.ts';
import type { CutsceneFlow } from './QuestMediaReferences';

export function cutsceneTimeline(
  flow: CutsceneFlow,
  choices: Record<string, string>,
  preference: 'ask' | 'male' | 'female',
  durations: Record<string, number>,
) {
  const clips: { id: string; start: number; duration: number; mediaStart: number }[] = [];
  const decisions: { id: string; time: number; unresolved: boolean }[] = [];
  const visited = new Set<string>();
  let id: string | null = flow.entry;
  let total = 0;
  let complete = true;
  while (id && !visited.has(id)) {
    visited.add(id);
    const node = flow.nodes.find((candidate) => candidate.id === id);
    if (!node) {
      complete = false;
      break;
    }
    if (node.kind === 'choice') {
      const selected = choices[node.id] || preferredRoverTarget(node, preference);
      decisions.push({ id: node.id, time: total, unresolved: !selected });
      id = selected || node.options[0]?.next || null;
    } else {
      const end = node.end ?? durations[node.segment || node.asset];
      if (end === undefined || !Number.isFinite(end)) {
        complete = false;
        break;
      }
      const duration = Math.max(0, end - node.start);
      clips.push({ id: node.id, start: total, duration, mediaStart: node.start });
      total += duration;
      id = node.next;
    }
  }
  if (id) complete = false;
  return { clips, decisions, total, complete };
}

export function timelineTarget(timeline: ReturnType<typeof cutsceneTimeline>, position: number) {
  const time = Math.max(0, Math.min(position, timeline.total));
  const decision = timeline.decisions.find((entry) => entry.unresolved && time >= entry.time);
  if (decision) return { id: decision.id, time: null };
  const clip =
    timeline.clips.find((entry) => time < entry.start + entry.duration) || timeline.clips.at(-1);
  return clip
    ? { id: clip.id, time: clip.mediaStart + Math.min(clip.duration, time - clip.start) }
    : null;
}

export function playbackVolume(master: number, music: number, role: string) {
  return Math.max(0, Math.min(1, master * (role === 'music' ? music : 1)));
}

export function chapterPosition(
  flow: CutsceneFlow,
  timeline: ReturnType<typeof cutsceneTimeline>,
  assetId: number | undefined,
  originalTime: number,
) {
  if (assetId === undefined || !Number.isFinite(originalTime)) return null;
  for (const clip of timeline.clips) {
    const node = flow.nodes.find((entry) => entry.id === clip.id);
    if (node?.kind !== 'clip') continue;
    const media = flow.media[node.segment || node.asset];
    if (!media) continue;
    const start = (media.timeline_offset || 0) + clip.mediaStart;
    if (
      media.asset_node_id === assetId &&
      originalTime >= start &&
      originalTime < start + clip.duration
    ) {
      return clip.start + originalTime - start;
    }
  }
  return null;
}
