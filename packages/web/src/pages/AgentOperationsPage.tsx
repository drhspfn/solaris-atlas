import { ArrowLeft, ArrowRight, Check, RefreshCw } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { api, ApiError } from '../api/client';
import { useAuth } from '../auth/AuthProvider';
import { APP_SETTINGS } from '../config/settings';
import type { AgentUsage } from '../data/storyAgent';

type Alert = {
  id: number;
  run_id: number;
  kind: string;
  text: string;
  node_id: number;
  citations: { node_id: number; quote: string; locale?: string }[];
};
type Alerts = { alerts: Alert[]; next_before: number | null };
const money = (value: string) => `$${Number(value).toFixed(4)}`;

export function AgentOperationsPage({ kind }: { kind: 'alerts' | 'usage' }) {
  const { refresh: refreshAuth } = useAuth();
  const [params, setParams] = useSearchParams();
  const before = params.get('before');
  const [data, setData] = useState<Alerts | AgentUsage | null>(null);
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
    const query =
      kind === 'alerts'
        ? `?limit=${APP_SETTINGS.storyAgent.pageSize}${before ? `&before=${encodeURIComponent(before)}` : ''}`
        : '';
    void api<Alerts | AgentUsage>(`/admin/story-agent/${kind}${query}`, {
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
          <h2>{kind === 'alerts' ? 'Alerts' : 'Usage'}</h2>
          <p>
            {kind === 'alerts'
              ? 'Missing sources, unresolved questions and processing failures.'
              : 'Daily spending, token usage and outstanding reservations.'}
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
        {notice || (updating ? 'Loading records…' : '')}
      </p>
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
      {data && 'days' in data && (
        <section className="content-panel agent-ledger">
          <div className="agent-panel-header">
            <h2>Daily ledger</h2>
          </div>
          <p>
            Cap {money(data.daily_budget_usd)} / day ·{' '}
            {data.daily_token_limit
              ? `${data.daily_token_limit.toLocaleString('en-US')} token cap`
              : 'No separate daily token cap'}{' '}
            · {data.timezone}
          </p>
          <p>
            Today: {data.today}. Costs include the safety margin. Reservations remain held until
            billing is confirmed; they are not completed charges.
          </p>
          <div className="agent-table-scroll" tabIndex={0} aria-label="Daily usage table">
            <table>
              <thead>
                <tr>
                  <th scope="col">Budget day</th>
                  <th scope="col">Spent</th>
                  <th scope="col">Reserved</th>
                  <th scope="col">Used tokens</th>
                  <th scope="col">Reserved tokens</th>
                </tr>
              </thead>
              <tbody>
                {data.days.map((day) => (
                  <tr key={day.day}>
                    <th scope="row">{day.day}</th>
                    <td>{money(day.spent_usd)}</td>
                    <td>{money(day.reserved_usd)}</td>
                    <td>{day.spent_tokens.toLocaleString('en-US')}</td>
                    <td>{day.reserved_tokens.toLocaleString('en-US')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!data.days.length && <p className="empty-inline">No spending recorded yet.</p>}
        </section>
      )}
    </section>
  );
}
