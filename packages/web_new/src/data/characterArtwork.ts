type Artwork = { kind: string; url: string | null; scope?: string };

/** An archive with no personal image must not fall back to an unclassified image. */
export function characterPortrait(artwork: readonly Artwork[]): string | null {
  const personal = artwork.filter(
    (art) => art.scope !== "shared_resource" && art.url,
  );
  const preferred =
    personal.find((art) => art.kind === "RoleHeadIconLarge") ||
    personal.find((art) => art.kind === "RoleHeadIconBig") ||
    personal.find((art) => art.kind === "RoleHeadIcon");
  return preferred?.url || personal[0]?.url || null;
}
