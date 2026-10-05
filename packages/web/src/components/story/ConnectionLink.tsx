import { useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link } from 'react-router-dom';

import { APP_SETTINGS } from '../../config/settings';
import type { StoryExplanation } from '../../data/explanations';

export function ConnectionLink({
  link,
  children,
}: {
  link: StoryExplanation['links'][number];
  children: React.ReactNode;
}) {
  const id = useId();
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const keepOpen = () => {
    if (closeTimer.current) clearTimeout(closeTimer.current);
  };
  const close = () => {
    keepOpen();
    closeTimer.current = setTimeout(
      () => setPosition(null),
      APP_SETTINGS.presentation.connectionPreviewCloseMs,
    );
  };
  useEffect(
    () => () => {
      if (closeTimer.current) clearTimeout(closeTimer.current);
    },
    [],
  );
  useEffect(() => {
    if (!position) return;
    const dismiss = () => setPosition(null);
    const dismissOutsideScroll = (event: Event) => {
      const preview = document.getElementById(id);
      if (event.target instanceof Node && preview?.contains(event.target)) return;
      dismiss();
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') dismiss();
    };
    window.addEventListener('keydown', escape);
    window.addEventListener('scroll', dismissOutsideScroll, true);
    window.addEventListener('resize', dismiss);
    return () => {
      window.removeEventListener('keydown', escape);
      window.removeEventListener('scroll', dismissOutsideScroll, true);
      window.removeEventListener('resize', dismiss);
    };
  }, [id, position]);
  if (!link.href) return <span>{children}</span>;
  return (
    <>
      <Link
        className="story-connection-link"
        to={link.href}
        aria-describedby={position ? id : undefined}
        onMouseEnter={(event) => {
          keepOpen();
          const rect = event.currentTarget.getBoundingClientRect();
          setPosition({
            left: Math.max(12, Math.min(rect.left, window.innerWidth - 332)),
            top: rect.bottom + 8,
          });
        }}
        onFocus={(event) => {
          keepOpen();
          const rect = event.currentTarget.getBoundingClientRect();
          setPosition({
            left: Math.max(12, Math.min(rect.left, window.innerWidth - 332)),
            top: rect.bottom + 8,
          });
        }}
        onMouseLeave={close}
        onBlur={close}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            keepOpen();
            setPosition(null);
          }
        }}
        onClick={() => {
          keepOpen();
          setPosition(null);
        }}
      >
        {children}
      </Link>
      {position &&
        createPortal(
          <aside
            id={id}
            role="tooltip"
            className="story-connection-preview"
            style={{ left: position.left, top: position.top }}
            ref={(element) => {
              if (!element) return;
              const rect = element.getBoundingClientRect();
              if (rect.bottom > window.innerHeight - 12) {
                element.style.top = `${Math.max(12, position.top - rect.height - 32)}px`;
              }
            }}
            onMouseEnter={keepOpen}
            onMouseLeave={close}
          >
            <small>AI interpretation{link.certainty ? ` · ${link.certainty}` : ''}</small>
            <strong>
              {link.relation_label || link.relation.replaceAll('_', ' ').toLowerCase()}
            </strong>
            <p>{link.explanation}</p>
            <small>Open link to read the connection and its sources.</small>
          </aside>,
          document.body,
        )}
    </>
  );
}
