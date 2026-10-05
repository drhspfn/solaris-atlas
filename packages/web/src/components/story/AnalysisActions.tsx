import { Play } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';

import { api, ApiError } from '../../api/client';
import { useAuth } from '../../auth/AuthProvider';
import { ActionMenu } from '../ui/ActionMenu';

type Target =
  { kind: 'quest'; questId: number } | { kind: 'cutscene'; assetIds: number[]; version: string };

export function AnalysisActions({ target }: { target: Target }) {
  const { user, refresh } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [job, setJob] = useState<number | null>(null);
  const pending = useRef(false);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  if (user?.role !== 'admin') return null;
  const cutscene = target.kind === 'cutscene';
  const path = cutscene ? '/admin/cutscene-analysis' : '/admin/story-agent';
  async function start() {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError('');
    const request = new AbortController();
    controller.current = request;
    try {
      const result = await api<{ id?: number; jobs?: { id: number }[] }>(
        cutscene ? '/admin/story-agent/cutscenes/jobs' : '/admin/story-agent/jobs',
        {
          method: 'POST',
          signal: request.signal,
          body:
            target.kind === 'quest'
              ? { quest_id: target.questId }
              : { asset_node_ids: target.assetIds, game_version: target.version },
        },
      );
      if (!request.signal.aborted) setJob(result.id ?? result.jobs?.[0]?.id ?? null);
    } catch (e) {
      if (!request.signal.aborted) {
        setError(e instanceof Error ? e.message : 'Analysis could not be queued. Try again.');
        if (e instanceof ApiError && [401, 403].includes(e.status)) void refresh();
      }
    } finally {
      pending.current = false;
      if (!request.signal.aborted) setBusy(false);
    }
  }
  return (
    <ActionMenu label={cutscene ? 'Cutscene actions' : 'Quest actions'}>
      <button
        type="button"
        disabled={busy || (target.kind === 'cutscene' && !target.assetIds.length)}
        onClick={() => void start()}
      >
        <Play size={15} aria-hidden="true" />{' '}
        {busy ? 'Queuing analysis…' : 'Analyze ' + (cutscene ? 'cutscene' : 'quest')}
      </button>
      <small>
        Uses the shared daily analysis budget.{cutscene && ' All imported variants are included.'}
      </small>
      {error && <p role="alert">{error}</p>}
      {job !== null && (
        <p role="status">Run #{job} saved. Matching requests reuse existing tasks.</p>
      )}
      <Link to={`${path}${job !== null ? `?job=${job}` : ''}`}>View analysis tasks</Link>
    </ActionMenu>
  );
}
