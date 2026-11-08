import type { SequenceMedia } from "./encounterTransitions";

export function hasEngineCinematicTracks(media?: SequenceMedia) {
  if (media?.schema_version !== 1 || !Array.isArray(media.sections)) return false;
  return media.sections.some((section) => section.kind === "camera") &&
    media.sections.some((section) => ["skeletal_animation", "skeletal_animation_asset"].includes(section.kind));
}

export function illustrationSections(media?: SequenceMedia) {
  if (media?.schema_version !== 1 || !Array.isArray(media.sections)) return [];
  return media.sections.filter(
    (section) =>
      section.kind === "illustration" &&
      section.status === "published" &&
      typeof section.url === "string" &&
      !!section.url &&
      typeof section.file_id === "number" &&
      Number.isSafeInteger(section.file_id),
  );
}

export function illustrationStatus(media?: SequenceMedia): string | null {
  const composition = media?.composition;
  if (!composition || composition.schema_version !== 1)
    return "This sequence is archived; illustrations have not been rendered.";
  const reasons = Array.isArray(composition.reasons) ? composition.reasons : [];
  if (
    reasons.some((reason) => /missing|not_found|unavailable/i.test(reason.code))
  )
    return "Illustrations could not be rendered because required assets are missing.";
  if (composition.state === "failed") return "Illustration rendering failed.";
  if (composition.state === "unsupported")
    return "This sequence is archived, but its composition is not supported for rendering.";
  if (composition.state === "ready")
    return "Rendered illustrations are not available.";
  return "Illustrations are not available.";
}
