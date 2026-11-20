import type { CharacterArchiveData } from "./CharacterArchive";
import type { MediaItem } from "../ui/MediaViewer";

export function artworkLabel(
  art: CharacterArchiveData["artwork"][number],
): string {
  if (art.scope === "shared_resource") {
    if (art.kind === "RolePortrait") return "Shared portrait reference";
    return art.kind === "RoleStand"
      ? "Shared quest artwork"
      : "Shared resource icon";
  }
  return art.kind.replaceAll(/([a-z])([A-Z])/g, "$1 $2");
}

export function collectCharacterMedia(
  artwork: CharacterArchiveData["artwork"],
  portraitUrl: string | null | undefined,
  name: string,
  trialUrl: string | null,
  normalize: (url: string) => string,
  showcaseVideos: CharacterArchiveData["showcase_videos"] = [],
): MediaItem[] {
  const items: MediaItem[] = [];
  const seen = new Set<string>();
  const add = (item: MediaItem) => {
    if (seen.has(item.src)) return;
    seen.add(item.src);
    items.push(item);
  };
  const posterArt = artwork.find(
    (art) =>
      art.kind === "RoleStand" && art.scope !== "shared_resource" && art.url,
  );
  const poster = posterArt?.url ? normalize(posterArt.url) : undefined;
  for (const art of artwork) {
    if (!art.url || art.scope === "shared_resource") continue;
    const src = normalize(art.url);
    add({ id: `artwork:${src}`, src, type: "image", title: artworkLabel(art) });
  }
  if (portraitUrl) {
    const src = normalize(portraitUrl);
    add({
      id: `portrait:${src}`,
      src,
      type: "image",
      title: `${name} portrait`,
    });
  }
  if (trialUrl) {
    add({
      id: `trial:${trialUrl}`,
      src: trialUrl,
      type: "video",
      title: "Trial animation",
      poster,
    });
  }
  for (const video of showcaseVideos ?? []) {
    if (!video?.url) continue;
    const src = normalize(video.url);
    const rawTitle = video.title || video.name || "Showcase video";
    const title =
      rawTitle
        .replace(/<\/?(?:color|ano|i|b|u|s|br|size|te)(?:\s*=[^>]*)?>/gi, "")
        .replace(/\s+/g, " ")
        .trim() || "Showcase video";
    add({
      id: `showcase:${video.id}:${src}`,
      src,
      type: "video",
      title,
      poster,
    });
  }
  return items;
}

export function characterMediaIndex(
  items: MediaItem[],
  src: string | null,
): number | null {
  if (!src) return null;
  const index = items.findIndex((item) => item.src === src);
  return index < 0 ? null : index;
}
