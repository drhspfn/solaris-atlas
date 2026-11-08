export type AnimationClip = {
  url: string;
  file_id: number;
  mime_type: "video/webm";
  animation: string;
  rover?: "male" | "female" | null;
  duration_seconds?: number;
  width?: number;
  height?: number;
  loop?: boolean;
};

export type AnimationPreview = {
  recipe: "riglyph-native-v1" | "riglyph-native-v2";
  status: "ready";
  default_animation?: string | null;
  variants: AnimationClip[];
};

/** Only published WebM objects can be played; a source ZIP is never a video. */
export function animationPreviewUrl(
  preview: AnimationClip | null | undefined,
): string | null {
  if (preview?.mime_type !== "video/webm") return null;
  try {
    const url = new URL(preview.url);
    return ["https:", "http:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

export function selectAnimationClip(
  preview: AnimationPreview | null | undefined,
  animation: string | null | undefined,
  rover = "",
): AnimationClip | null {
  if ((preview?.recipe !== "riglyph-native-v1" && preview?.recipe !== "riglyph-native-v2") || preview.status !== "ready")
    return null;
  animation ??= preview.default_animation;
  if (!animation) return null;
  const clips = preview.variants;
  const named = clips.filter(
    (clip) => clip.animation === animation && animationPreviewUrl(clip),
  );
  return (
    named.find((clip) => clip.rover === rover) ??
    named.find((clip) => !clip.rover) ??
    null
  );
}
