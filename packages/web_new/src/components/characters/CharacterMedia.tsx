"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { Image as ImageIcon, Play } from "lucide-react";
import { apiUrl } from "@/api/client";
import { Button } from "@/components/ui/Button";
import type { MediaItem } from "@/components/ui/MediaViewer";
import { useMediaViewer } from "@/components/ui/MediaViewerProvider";
import {
  animationPreviewUrl,
  selectAnimationClip,
} from "@/components/ui/animationPreview";
import type { CharacterArchiveData } from "./CharacterArchive";
import {
  characterMediaIndex,
  collectCharacterMedia,
} from "./characterMediaItems";
import "@/styles/character-media.css";

const normalize = (src: string) => apiUrl(src.replace(/^\/api(?=\/|$)/, ""));
type CharacterMediaContextValue = {
  items: MediaItem[];
  open: (index: number) => void;
  openSource: (src: string) => void;
};
const empty: CharacterMediaContextValue = {
  items: [],
  open: () => {},
  openSource: () => {},
};
const CharacterMediaContext = createContext<CharacterMediaContextValue>(empty);

export function useCharacterMedia() {
  return useContext(CharacterMediaContext);
}

export function CharacterMediaProvider({
  archive,
  portraitUrl,
  name,
  children,
}: {
  archive: CharacterArchiveData | null;
  portraitUrl?: string | null;
  name: string;
  children: ReactNode;
}) {
  const items = useMemo(
    () =>
      collectCharacterMedia(
        archive?.artwork ?? [],
        portraitUrl,
        name,
        animationPreviewUrl(
          selectAnimationClip(archive?.trial_spine?.animation_preview, "idle"),
        ),
        normalize,
      ),
    [archive, portraitUrl, name],
  );
  const { open: openViewer } = useMediaViewer();
  const open = useCallback(
    (index: number) => {
      if (items[index]) openViewer(items, index);
    },
    [items, openViewer],
  );
  const openSource = useCallback(
    (src: string) => {
      const index = characterMediaIndex(items, normalize(src));
      if (index !== null) openViewer(items, index);
    },
    [items, openViewer],
  );
  const value = useMemo(
    () => ({ items, open, openSource }),
    [items, open, openSource],
  );
  return (
    <CharacterMediaContext.Provider value={value}>
      {children}
    </CharacterMediaContext.Provider>
  );
}

function MediaCard({ item, onOpen }: { item: MediaItem; onOpen: () => void }) {
  const [failed, setFailed] = useState(false);
  const thumbnail = item.type === "image" ? item.src : item.poster;
  return (
    <Button
      variant="secondary"
      className="character-media-card"
      onClick={onOpen}
      aria-label={`View ${item.title}`}
    >
      <span className="character-media-thumbnail">
        {item.type === "video" && !failed ? (
          <video
            src={`${item.src.split("#")[0]}#t=0.001`}
            poster={item.poster}
            muted
            playsInline
            preload="metadata"
            onLoadedMetadata={(event) => {
              event.currentTarget.currentTime = 0.001;
            }}
            aria-hidden="true"
            tabIndex={-1}
            onLoadedData={(event) => event.currentTarget.pause()}
            onError={() => setFailed(true)}
          />
        ) : thumbnail && !failed ? (
          <img
            src={thumbnail}
            alt=""
            loading="lazy"
            onError={() => setFailed(true)}
          />
        ) : (
          <span className="character-media-placeholder">
            <ImageIcon size={24} aria-hidden="true" />
            {failed && <span>Preview unavailable</span>}
          </span>
        )}
        {item.type === "video" && (
          <span className="character-media-video-label">
            <Play size={13} aria-hidden="true" /> Animation
          </span>
        )}
      </span>
      <span className="character-media-caption">{item.title}</span>
      <span className="character-media-action">
        View {item.type === "video" ? "animation" : "image"}
      </span>
    </Button>
  );
}

export function CharacterMediaGallery() {
  const { items, open } = useCharacterMedia();
  if (!items.length) return null;
  return (
    <section className="character-media" aria-label="Character media">
      <h3>Artwork and animation</h3>
      <div className="character-media-grid">
        {items.map((item, index) => (
          <MediaCard key={item.id} item={item} onOpen={() => open(index)} />
        ))}
      </div>
    </section>
  );
}

export function CharacterMediaTrigger({
  src,
  label,
  children,
  className,
}: {
  src: string;
  label: string;
  children: ReactNode;
  className?: string;
}) {
  const { openSource } = useCharacterMedia();
  return (
    <Button
      className={className}
      aria-label={label}
      onClick={() => openSource(src)}
    >
      {children}
    </Button>
  );
}
