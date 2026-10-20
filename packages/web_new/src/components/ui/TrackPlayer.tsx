'use client';

import { useId, useRef, useState } from 'react';
import { Settings2 } from 'lucide-react';
import { AudioPlayer } from './AudioPlayer';

export function TrackPlayer({
  src,
  tracks,
  defaultTrack,
  label = 'Audio',
}: {
  src?: string | null;
  tracks?: Record<string, string>;
  defaultTrack?: string;
  label?: string;
}) {
  const [choice, setChoice] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const id = useId();
  const names = Object.keys(tracks ?? {});
  if (!tracks || !names.length) return <AudioPlayer src={src} label={label} />;
  const selected = choice ?? defaultTrack ?? names[0];
  const source = choice ? tracks[choice] : (tracks[selected] ?? src);
  return (
    <AudioPlayer
      src={source}
      label={label}
      settings={
        <>
          <button
            ref={trigger}
            type="button"
            aria-label={`Player settings: ${label}`}
            aria-haspopup="menu"
            aria-expanded={open}
            aria-controls={id}
            onClick={() => {
              if (!menu.current || !trigger.current) return;
              if (open) {
                menu.current.hidePopover();
                return;
              }
              const rect = trigger.current.getBoundingClientRect();
              const above = rect.top > 220;
              const width = Math.min(220, window.innerWidth - 24);
              Object.assign(menu.current.style, {
                width: `${width}px`,
                left: `${Math.max(12, Math.min(rect.right - width, window.innerWidth - width - 12))}px`,
                top: `${above ? rect.top - 4 : rect.bottom + 4}px`,
                transform: above ? 'translateY(-100%)' : 'none',
                maxHeight: `${Math.max(60, Math.min(260, above ? rect.top - 16 : window.innerHeight - rect.bottom - 16))}px`,
              });
              menu.current.showPopover();
              menu.current.querySelector<HTMLButtonElement>('[aria-checked="true"]')?.focus();
            }}
          >
            <Settings2 size={16} />
          </button>
          <div
            id={id}
            ref={menu}
            popover="auto"
            className="audio-settings-menu"
            role="menu"
            aria-label={`Audio track: ${label}`}
            onToggle={(event) => setOpen(event.newState === 'open')}
            onKeyDown={(event) => {
              if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
              event.preventDefault();
              const buttons = [
                ...(menu.current?.querySelectorAll<HTMLButtonElement>('button') ?? []),
              ];
              const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
              const next =
                event.key === 'Home'
                  ? 0
                  : event.key === 'End'
                    ? buttons.length - 1
                    : (index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) %
                      buttons.length;
              buttons[next]?.focus();
            }}
          >
            <span>Track</span>
            {names.map((name) => (
              <button
                type="button"
                role="menuitemradio"
                key={name}
                aria-checked={selected === name}
                onClick={() => {
                  setChoice(name);
                  menu.current?.hidePopover();
                  trigger.current?.focus({ preventScroll: true });
                }}
              >
                {name}
              </button>
            ))}
          </div>
        </>
      }
    />
  );
}
