'use client';

import { SkeletonGroup, TextSkeleton } from '@/components/ui/Skeleton';
import { ArrowLeft, ArrowRight, Check, RefreshCw } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { api, ApiError } from '@/api/client';
import { useAuth } from '@/auth/AuthProvider';
import { APP_SETTINGS } from '@/config/settings';
import { Link, useSearchParams } from '@/lib/router';

type Alert = {
  id: number;
  run_id: number;
  kind: string;
  text: string;
  node_id: number;
  citations: { node_id: number; quote: string; locale?: string }[];
};
type Alerts = { alerts: Alert[]; next_before: number | null };

export function AgentOperationsSkeleton({ kind }: { kind: 'alerts' }) {
  return (
    <SkeletonGroup label={`Loading ${kind}`}>
      <div className="admin-alerts">
        {[0, 1, 2].map((row) => (
          <article className="content-panel admin-alert" key={row}>
            <TextSkeleton lines={3} />
          </article>
        ))}
      </div>
    </SkeletonGroup>
  );
}

export function AgentOperationsClient({ kind }: { kind: 'alerts' }) {
  const { refresh: refreshAuth } = useAuth();
  const [params, setParams] = useSearchParams();
  const before = params.get('before');
  const [data, setData] = useState<Alerts | null>(null);
  const [error, setError] = useState('');
  const [actionError, setActionError] = useState('');
  const [notice, setNotice] = useState('');
  const [updating, setUpdating] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [busy, setBusy] = useState<number | null>(null);
  const operation = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    setUpdating(true);
    const query = `?limit=${APP_SETTINGS.storyAgent.pageSize}${before ? `&before=${encodeURIComponent(before)}` : ''}`;
    void api<Alerts>(`/admin/story-agent/${kind}${query}`, {
      signal: controller.signal,
    })
      .then((value) => {
        if (!controller.signal.aborted) {
          setData(value);
          setError('');
        }
      })
      .catch((e) => {
        if (!controller.signal.aborted) {
          setError(e instanceof Error ? e.message : 'Request failed. Try again.');
          if (e instanceof ApiError && [401, 403].includes(e.status)) void refreshAuth();
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setUpdating(false);
      });
    return () => controller.abort();
  }, [kind, before, refresh, refreshAuth]);

  async function resolve(id: number) {
    if (operation.current) return;
    operation.current = true;
    setBusy(id);
    setActionError('');
    try {
      await api(`/admin/story-agent/alerts/${id}/resolve`, { method: 'POST' });
      setNotice(`Alert #${id} resolved.`);
      setRefresh((value) => value + 1);
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Could not resolve this alert. Try again.');
    } finally {
      operation.current = false;
      setBusy(null);
    }
  }

  return (
    <section className="agent-page">
      <header className="page-heading agent-heading">
        <div>
          <h2>Alerts</h2>
          <p>
            Missing sources, unresolved questions and processing failures.
          </p>
        </div>
        <button
          className="agent-button"
          onClick={() => setRefresh((value) => value + 1)}
          disabled={updating || busy !== null}
        >
          <RefreshCw size={14} />
          Refresh
        </button>
      </header>
      {(error || actionError) && (
        <p className="agent-feedback" role="alert">
          {actionError || error}
        </p>
      )}
      <p className="agent-notice" role="status">
        {notice}
      </p>
      {!data && updating && !error && <AgentOperationsSkeleton kind={kind} />}
      {data && 'alerts' in data && (
        <>
          {!data.alerts.length && <p className="empty-inline">No open alerts on this page.</p>}
          <div className="admin-alerts">
            {data.alerts.map((alert) => (
              <article className="content-panel admin-alert" key={alert.id}>
                <header>
                  <strong>Alert #{alert.id}</strong>
                  <span className="agent-status">{alert.kind.replaceAll('_', ' ')}</span>
                </header>
                <p>{alert.text}</p>
                {!!alert.citations.length && (
                  <details>
                    <summary>Source citations</summary>
                    {alert.citations.map((citation, index) => (
                      <blockquote key={index}>
                        {citation.quote}
                        <small>
                          Node {citation.node_id} · {citation.locale ?? 'source'}
                        </small>
                      </blockquote>
                    ))}
                  </details>
                )}
                <footer>
                  <Link className="agent-button" to={`/admin/story-agent?job=${alert.run_id}`}>
                    Run #{alert.run_id}
                    <ArrowRight size={13} />
                  </Link>
                  <button
                    className="agent-button"
                    disabled={busy !== null}
                    aria-busy={busy === alert.id}
                    onClick={() => void resolve(alert.id)}
                  >
                    <Check size={14} />
                    Mark resolved
                  </button>
                </footer>
              </article>
            ))}
          </div>
          <div className="agent-pagination">
            {before && (
              <button className="agent-button" onClick={() => setParams({})}>
                <ArrowLeft size={13} />
                Newest
              </button>
            )}
            {data.next_before && (
              <button
                className="agent-button"
                onClick={() => setParams({ before: String(data.next_before) })}
              >
                Older alerts
                <ArrowRight size={13} />
              </button>
            )}
          </div>
        </>
      )}

    </section>
  );
}
