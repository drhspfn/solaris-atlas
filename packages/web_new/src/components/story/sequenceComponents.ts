import type { SequenceMedia } from "./encounterTransitions";

type Section = SequenceMedia["sections"][number];

// Bounds use Unreal's Exclusive=0, Inclusive=1, Open=2 enum. Each range
// belongs to its own MovieScene clock, never to a guessed global movie time.
export function sequenceInterval(section: Section) {
  const clock = section.clock as
    | { numerator?: number; denominator?: number }
    | undefined;
  const range = section.range as
    | { Value?: { LowerBound?: any; UpperBound?: any } }
    | undefined;
  const lower = range?.Value?.LowerBound,
    upper = range?.Value?.UpperBound;
  if (
    !clock ||
    !Number.isSafeInteger(clock.numerator) ||
    !Number.isSafeInteger(clock.denominator) ||
    clock.numerator! <= 0 ||
    clock.denominator! <= 0 ||
    !lower ||
    !upper ||
    ![0, 1].includes(lower.Type) ||
    ![0, 1].includes(upper.Type) ||
    !Number.isSafeInteger(lower.Value?.Value) ||
    !Number.isSafeInteger(upper.Value?.Value)
  )
    return null;
  const start = lower.Value.Value + (lower.Type === 0 ? 1 : 0);
  const end = upper.Value.Value + (upper.Type === 1 ? 1 : 0);
  if (end <= start) return null;
  return {
    start: (start * clock.denominator!) / clock.numerator!,
    end: (end * clock.denominator!) / clock.numerator!,
    scene: section.scene_source,
  };
}

export function sequenceTime(seconds: number) {
  const value = Math.abs(seconds);
  return `${seconds < 0 ? "−" : ""}${Math.floor(value / 60)}:${(value % 60).toFixed(2).padStart(5, "0")}`;
}

export function componentOccurrences(
  media: SequenceMedia | undefined,
  fileId: number,
) {
  return (media?.sections || [])
    .filter(
      (section) =>
        section.kind === "movie_fragment" &&
        section.status === "published" &&
        section.file_id === fileId,
    )
    .map((section) => ({
      source: section.source,
      interval: sequenceInterval(section),
      // Preserve offsets and compositing bindings as evidence, not crop commands.
      parameters: section.parameters,
      role: section.composition_role,
      rover: section.rover,
      mediaTexture: section.media_texture,
      materialBindings: section.material_bindings,
    }));
}

export function orderComponents(sections: Section[]) {
  const scenes = new Map<string | Section, Section[]>();
  for (const section of sections) {
    const key =
      typeof section.scene_source === "string" ? section.scene_source : section;
    scenes.set(key, [...(scenes.get(key) || []), section]);
  }
  return [...scenes.values()].flatMap((group) =>
    group.toSorted((a, b) => {
      const left = sequenceInterval(a),
        right = sequenceInterval(b);
      if (!left && !right) return 0;
      if (!left) return 1;
      if (!right) return -1;
      return left.start - right.start;
    }),
  );
}
