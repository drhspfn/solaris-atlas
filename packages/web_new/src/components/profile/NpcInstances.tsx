'use client';

import { ArrowUpRight, MapPin } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '@/api/client';
import { ErrorPanel } from '@/components/ui/Feedback';
import { Pagination } from '@/components/ui/Pagination';
import { useLocale } from '@/hooks/useLocale';
import { Link } from '@/lib/router';
import '@/styles/npc-context.css';

const PAGE_SIZE = 8;
type Location = { id: number; names: Record<string, string>; map_names: Record<string, string>; href: string };
type LocationPage = { results: Location[]; total: number };

export function NpcInstances({ characterKey, gameVersion }: { characterKey: string; gameVersion?: string }) {
  const locale = useLocale();
  const [page, setPage] = useState(1);
  const [result, setResult] = useState<LocationPage | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({ locale, limit: String(PAGE_SIZE), offset: String((page - 1) * PAGE_SIZE) });
    if (gameVersion) params.set('game_version', gameVersion);
    setResult(null); setError('');
    api<LocationPage>(`/npcs/${encodeURIComponent(characterKey)}/locations?${params}`, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setResult(value); })
      .catch((cause: Error) => { if (!controller.signal.aborted) setError(cause.message); });
    return () => controller.abort();
  }, [characterKey, gameVersion, locale, page, retry]);
  if (result && !result.total) return null;
  const name = (names: Record<string, string>) => names[locale] || names.en || Object.values(names)[0];
  return <section className="content-panel full-panel npc-context-section" aria-label="Where to meet">
    <div className="npc-context-heading"><h2><MapPin size={19} aria-hidden="true" />Where to meet</h2>
      {result && <span>{result.total} {result.total === 1 ? 'location' : 'locations'}</span>}</div>
    {error ? <ErrorPanel message={error} onRetry={() => setRetry(value => value + 1)} /> : !result ?
      <p className="npc-context-loading" role="status">Finding map locations…</p> : <>
        <div className="npc-location-grid">{result.results.map(location =>
          <Link className="npc-location-card" key={location.id} to={location.href}>
            <MapPin size={20} aria-hidden="true" />
            <span><strong>{name(location.names) || 'World encounter'}</strong>
              {name(location.map_names) && name(location.map_names) !== name(location.names) && <small>{name(location.map_names)}</small>}
              <span className="npc-context-action">View on map</span></span>
            <ArrowUpRight size={17} aria-hidden="true" />
          </Link>)}</div>
        {result.total > PAGE_SIZE && <Pagination mode="pages" page={page} pageSize={PAGE_SIZE} total={result.total}
          onPageChange={setPage} ariaLabel="Location pages" />}
      </>}
  </section>;
}
