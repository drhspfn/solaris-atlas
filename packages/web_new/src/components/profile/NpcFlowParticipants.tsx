'use client';

import { useEffect, useState } from 'react';
import { api } from '@/api/client';
import { ErrorPanel } from '@/components/ui/Feedback';
import { useLocale } from '@/hooks/useLocale';

type Result = { results: { speaker: { canonical_key: string; label?: string }; flow_state: string }[]; truncated: boolean };

export function NpcFlowParticipants({ characterKey, gameVersion }: { characterKey: string; gameVersion?: string }) {
  const locale = useLocale();
  const [data, setData] = useState<Result | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({ locale });
    if (gameVersion) params.set('game_version', gameVersion);
    setData(null); setError('');
    api<Result>(`/npcs/${encodeURIComponent(characterKey)}/flow-participants?${params}`, { signal: controller.signal })
      .then((value) => { if (!controller.signal.aborted) setData(value); })
      .catch((cause: Error) => { if (!controller.signal.aborted) setError(cause.message); });
    return () => controller.abort();
  }, [characterKey, gameVersion, locale, retry]);
  return <div><h3>Speakers in referenced states</h3>
    <p>These speakers appear in referenced states; this does not identify them as this NPC.</p>
    {error ? <ErrorPanel message={error} onRetry={() => setRetry((value) => value + 1)} /> : !data ? <p role="status">Loading speakers…</p> : <>
      {!data.results.length && <p>No speaker evidence found.</p>}
      {data.results.map((entry) => <div className="plain-row" key={`${entry.speaker.canonical_key}:${entry.flow_state}`}>
        <span>{entry.speaker.label || entry.speaker.canonical_key}</span><small>{entry.flow_state}</small>
      </div>)}
      {data.truncated && <p>Only the first 100 references or speakers are shown.</p>}
    </>}
  </div>;
}
