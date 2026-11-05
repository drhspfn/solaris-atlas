'use client';

import { useEffect, useState } from 'react';
import { PlayerText, type PlayerDisplay } from '../dialogue/PlayerText';
import { sidebarWindow, type SidebarEntry } from './questSidebarModel';
import styles from './QuestSidebar.module.css';

export type SidebarGroup = {
  id: 'dialogue' | 'scenes' | 'activities';
  title: string;
  entries: SidebarEntry[];
};

export function QuestSidebar({ groups, display, activeAnchor = '' }: {
  groups: SidebarGroup[];
  display: PlayerDisplay;
  activeAnchor?: string;
}) {
  const [expanded, setExpanded] = useState(true);
  const [openGroup, setOpenGroup] = useState<string | null>('dialogue');
  const [hash, setHash] = useState('');
  const [queries, setQueries] = useState<Record<string, string>>({});
  const [limits, setLimits] = useState<Record<string, number>>({});
  useEffect(() => {
    const media = window.matchMedia('(max-width: 760px)');
    const resize = () => setExpanded(!media.matches);
    resize();
    media.addEventListener('change', resize);
    return () => media.removeEventListener('change', resize);
  }, []);
  useEffect(() => {
    const reveal = () => setHash(window.location.hash);
    reveal();
    window.addEventListener('hashchange', reveal);
    return () => window.removeEventListener('hashchange', reveal);
  }, []);
  const selected = hash || (activeAnchor ? `#${activeAnchor}` : '');
  const activeGroup = groups.find((item) => item.entries.some((entry) => entry.href === selected))?.id;
  useEffect(() => {
    if (activeGroup) setOpenGroup(activeGroup);
  }, [selected, activeGroup]);
  return (
    <aside className={styles.sidebar} aria-label="Quest navigation">
      <details className={styles.container} open={expanded}>
        <summary className={styles.heading} onClick={(event) => {
          event.preventDefault(); setExpanded(!expanded);
        }}>In this quest</summary>
        <div className={styles.scroll}>
          {groups.map((group) => {
            const query = queries[group.id] || '';
            const page = sidebarWindow(group.entries, query, limits[group.id] || 10, selected);
            const inView = group.entries.filter((entry) => entry.inView).length;
            return (
              <details className={styles.group} key={group.id} open={openGroup === group.id}>
                <summary onClick={(event) => {
                  event.preventDefault(); setOpenGroup(openGroup === group.id ? null : group.id);
                }}>
                  {group.title}<span>{group.entries.length}</span>
                </summary>
                <p className={styles.count}>{inView} in this view · {group.entries.length} loaded</p>
                {group.entries.length > 10 && (
                  <label className={styles.search}>Search {group.title.toLocaleLowerCase()}
                    <input type="search" value={query} onChange={(event) => {
                      setQueries({ ...queries, [group.id]: event.target.value });
                      setLimits({ ...limits, [group.id]: 10 });
                    }} />
                  </label>
                )}
                {page.entries.length ? (
                  <nav aria-label={`Quest ${group.title.toLocaleLowerCase()}`}>
                    {page.entries.map((entry) => {
                      const content = <><span className={styles.title}><PlayerText value={entry.title} fallback={entry.fallback} display={display} /></span>
                        {entry.note && <small>{entry.note}</small>}</>;
                      return entry.href
                        ? <a key={entry.id} href={entry.href} aria-current={entry.href === selected ? 'location' : undefined}>{content}</a>
                        : <div className={styles.unavailable} key={entry.id}>{content}<small>Outside this view</small></div>;
                    })}
                  </nav>
                ) : <p className={styles.count}>{query ? 'No matching entries.' : 'No entries in this view.'}</p>}
                {!!group.entries.length && <p className={styles.count}>Showing {page.entries.length ? `${page.start + 1}–${page.start + page.entries.length}` : '0'} of {page.total}{query ? ' matches' : ' loaded'}</p>}
                {page.entries.length < page.total && <button className={styles.more} type="button" onClick={() => {
                  setLimits({ ...limits, [group.id]: page.entries.length + 10 });
                }}>Show {Math.min(10, page.total - page.entries.length)} more</button>}
              </details>
            );
          })}
        </div>
      </details>
    </aside>
  );
}
