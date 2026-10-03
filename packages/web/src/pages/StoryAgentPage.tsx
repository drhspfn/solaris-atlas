import '../styles/story-agent.css';

import { ArrowLeft, ArrowRight, BookOpen, Play, RefreshCw } from 'lucide-react';
import { type FormEvent, useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { api, ApiError } from '../api/client';
import { useAuth } from '../auth/AuthProvider';
import { APP_SETTINGS } from '../config/settings';
import {
  type AgentDetail,
  type AgentJob,
  type AgentUsage,
  resumeBody,
  resumePolicy,
  statusLabel,
} from '../data/storyAgent';

const money = (value: string | number | null | undefined) =>
  new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 3,
    maximumFractionDigits: 4,
  }).format(Number(value ?? 0));
const number = (value: number | null | undefined) => (value ?? 0).toLocaleString('en-US');
const message = (error: unknown) =>
  error instanceof Error ? error.message : 'Request failed. Try again.';
type JobsResponse = { jobs: AgentJob[]; next_before: number | null };

function Status({ status }: { status: string }) {
  return (
    <span
      className={`agent-status ${status === 'completed' ? 'done' : status === 'running' ? 'active' : status.startsWith('paused') || status === 'failed' ? 'paused' : ''}`}
    >
      {statusLabel(status)}
    </span>
  );
}

export function StoryAgentPage() {
  return <AgentWorkspace />;
}

function AgentWorkspace() {
  const { refresh: refreshAuth } = useAuth();
  const [params, setParams] = useSearchParams();
  const before = params.get('before');
  const [data, setData] = useState<JobsResponse | null>(null);
  const [usage, setUsage] = useState<AgentUsage | null>(null);
  const [error, setError] = useState('');
  const [actionError, setActionError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const mutation = useRef(false);
  const [refresh, setRefresh] = useState(0);
  const [updating, setUpdating] = useState(true);
  const selected = params.get('job') ?? (data?.jobs[0]?.id.toString() || null);

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (!mutation.current && document.visibilityState === 'visible')
        setRefresh((value) => value + 1);
    }, APP_SETTINGS.storyAgent.refreshMs);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setUpdating(true);
    void Promise.all([
      api<JobsResponse>(
        `/admin/story-agent/jobs?limit=${APP_SETTINGS.storyAgent.pageSize}${before ? `&before=${encodeURIComponent(before)}` : ''}`,
        { signal: controller.signal },
      ),
      api<AgentUsage>('/admin/story-agent/usage', { signal: controller.signal }),
    ])
      .then(([jobs, costs]) => {
        if (!controller.signal.aborted) {
          setData(jobs);
          setUsage(costs);
          setError('');
        }
      })
      .catch((e) => {
        if (!controller.signal.aborted) {
          setError(message(e));
          if (e instanceof ApiError && [401, 403].includes(e.status)) void refreshAuth();
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setUpdating(false);
      });
    return () => controller.abort();
  }, [before, refresh, refreshAuth]);

  function selectJob(id: number) {
    setParams((previous) => {
      const next = new URLSearchParams(previous);
      next.set('job', String(id));
      return next;
    });
    setNotice('');
  }
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!event.currentTarget.reportValidity()) return;
    if (mutation.current) return;
    const values = new FormData(event.currentTarget);
    mutation.current = true;
    setBusy(true);
    setNotice('');
    setActionError('');
    try {
      const result = await api<{ id: number; status: string }>('/admin/story-agent/jobs', {
        method: 'POST',
        body: {
          quest_id: Number(values.get('quest')),
        },
      });
      setParams({ job: String(result.id) });
      setNotice(
        `Run #${result.id}: ${statusLabel(result.status)}. Existing matching requests reuse the same run.`,
      );
      setRefresh((value) => value + 1);
    } catch (e) {
      setActionError(message(e));
      if (e instanceof ApiError && [401, 403].includes(e.status)) void refreshAuth();
    } finally {
      mutation.current = false;
      setBusy(false);
    }
  }
  async function resume(job: AgentDetail, steps: number) {
    if (mutation.current) return;
    mutation.current = true;
    setBusy(true);
    setNotice('');
    setActionError('');
    try {
      const result = await api<{ status: string }>(`/admin/story-agent/jobs/${job.id}/resume`, {
        method: 'POST',
        body: resumeBody(job, steps),
      });
      setNotice(`Run #${job.id}: ${statusLabel(result.status)}. Continuing from step ${job.step}.`);
      setRefresh((value) => value + 1);
    } catch (e) {
      setActionError(message(e));
      if (e instanceof ApiError && [401, 403].includes(e.status)) void refreshAuth();
    } finally {
      mutation.current = false;
      setBusy(false);
    }
  }
  const today = usage?.days.find((day) => day.day === usage.today);
  return (
    <div className="agent-page">
      <header className="page-heading agent-heading">
        <div>
          <h2>Story agent</h2>
          <p>Queue quests, inspect progress and continue saved research.</p>
        </div>
        <button
          className="agent-button"
          disabled={updating || busy}
          onClick={() => setRefresh((value) => value + 1)}
          aria-busy={updating}
        >
          <RefreshCw size={14} />
          Refresh
        </button>
      </header>
      <section className="agent-budget" aria-label="Daily allowance">
        <div>
          <span>Estimated spend today</span>
          <strong>{usage ? money(today?.spent_usd) : '—'}</strong>
        </div>
        <div>
          <span>Reserved</span>
          <strong>{usage ? money(today?.reserved_usd) : '—'}</strong>
        </div>
        <div>
          <span>Daily cap</span>
          <strong>{usage ? money(usage.daily_budget_usd) : '—'}</strong>
        </div>
        <div>
          <span>{usage?.daily_token_limit ? 'Tokens today / cap' : 'Tokens today'}</span>
          <strong>
            {usage
              ? `${number((today?.spent_tokens ?? 0) + (today?.reserved_tokens ?? 0))}${usage.daily_token_limit ? ` / ${number(usage.daily_token_limit)}` : ''}`
              : '—'}
          </strong>
        </div>
        <p>
          Estimated costs include the configured safety margin.{' '}
          {usage && `Budget day: ${usage.today} · ${usage.timezone}.`} Resuming never raises the
          daily cap. {usage?.daily_token_limit === 0 && 'No separate daily token cap.'}
        </p>
      </section>
      {(error || actionError) && (
        <div className="agent-feedback error" role="alert">
          <span>{actionError || error}</span>
          <button
            className="agent-button"
            onClick={() => setRefresh((value) => value + 1)}
            disabled={busy}
          >
            Refresh data
          </button>
        </div>
      )}
      <p className="agent-notice" role="status">
        {notice ||
          (updating ? 'Updating runs…' : 'Updates every 10 seconds while this page is visible.')}
      </p>
      <details className="content-panel agent-create">
        <summary>
          <Play size={15} />
          Analyze a quest
        </summary>
        <form className="auth-form agent-create-form" noValidate onSubmit={create}>
          <label>
            Quest ID
            <input
              name="quest"
              type="number"
              min="1"
              step="1"
              required
              placeholder="139000025"
              disabled={busy}
            />
          </label>
          <button className="agent-button primary" disabled={busy}>
            <Play size={14} />
            Queue analysis
          </button>
          <p>
            English analysis using the latest available quest transcript and sources from all
            imported patches.
          </p>
        </form>
      </details>
      <div className="agent-workspace">
        <section className="content-panel agent-list" aria-labelledby="runs-heading">
          <div className="agent-panel-header">
            <h2 id="runs-heading">Analysis runs</h2>
            <small>{data ? `${data.jobs.length} shown` : 'Loading…'}</small>
          </div>
          {!data && !error && (
            <p className="empty-inline" role="status">
              Loading analysis runs…
            </p>
          )}
          {data?.jobs.length === 0 && (
            <p className="empty-inline">
              No runs on this page. Queue a quest to start researching.
            </p>
          )}
          {data?.jobs.map((job) => (
            <button
              key={job.id}
              className={`agent-run ${String(job.id) === selected ? 'selected' : ''}`}
              onClick={() => selectJob(job.id)}
              aria-pressed={String(job.id) === selected}
            >
              <span className="agent-run-top">
                <strong>Run #{job.id}</strong>
                <Status status={job.status} />
              </span>
              <span>
                Quest {job.request?.quest_id ?? '—'} <small>· {job.request?.game_version}</small>
              </span>
              <span className="agent-run-bottom">
                <small>
                  {job.step} / {job.max_steps} steps · {job.model}
                </small>
                <strong>{money(job.cost_usd)}</strong>
              </span>
            </button>
          ))}
          <div className="agent-pagination">
            {before && (
              <button className="agent-button" onClick={() => setParams({})}>
                <ArrowLeft size={13} />
                Newest
              </button>
            )}
            {data?.next_before && (
              <button
                className="agent-button"
                onClick={() => setParams({ before: String(data.next_before) })}
              >
                Older runs
                <ArrowRight size={13} />
              </button>
            )}
          </div>
        </section>
        {selected ? (
          <RunDetail key={selected} id={selected} refresh={refresh} busy={busy} resume={resume} />
        ) : (
          <section className="content-panel agent-detail">
            <p className="empty-inline">Select an analysis run to inspect its progress.</p>
          </section>
        )}
      </div>
      {usage &&
        usage.days.some((day) => Number(day.reserved_usd) > 0 && day.day !== usage.today) && (
          <details className="content-panel agent-ledger">
            <summary>Earlier budget days with reservations</summary>
            <p>
              Unresolved requests keep their original reservation until provider billing is
              verified.
            </p>
            {usage.days
              .filter((day) => Number(day.reserved_usd) > 0 && day.day !== usage.today)
              .map((day) => (
                <p key={day.day}>
                  {day.day} · {money(day.reserved_usd)} reserved
                </p>
              ))}
          </details>
        )}
    </div>
  );
}

function RunDetail({
  id,
  refresh,
  busy,
  resume,
}: {
  id: string;
  refresh: number;
  busy: boolean;
  resume: (job: AgentDetail, steps: number) => Promise<void>;
}) {
  const [job, setJob] = useState<AgentDetail | null>(null);
  const [error, setError] = useState('');
  const [steps, setSteps] = useState<number>(APP_SETTINGS.storyAgent.extraSteps);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void api<AgentDetail>(`/admin/story-agent/jobs/${encodeURIComponent(id)}`, {
      signal: controller.signal,
    })
      .then((value) => {
        if (!controller.signal.aborted) {
          setJob(value);
          setError('');
        }
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(message(e));
      });
    return () => controller.abort();
  }, [id, refresh, retry]);
  const policy = job ? resumePolicy(job) : null;
  return (
    <section className="content-panel agent-detail" aria-labelledby="run-heading">
      <div className="agent-panel-header">
        <h2 id="run-heading">Run #{id}</h2>
        {job && <Status status={job.status} />}
      </div>
      {error && (
        <div className="agent-feedback error" role="alert">
          {error}
          <button className="agent-button" onClick={() => setRetry((value) => value + 1)}>
            Retry
          </button>
        </div>
      )}
      {!job && !error && (
        <p className="empty-inline" role="status">
          Loading run details…
        </p>
      )}
      {job && (
        <>
          <div className="agent-detail-body">
            <div className="agent-detail-meta">
              <span>
                Quest {job.request?.quest_id} · {job.request?.game_version} · {job.request?.locale}
              </span>
              <span>{job.limits.model}</span>
            </div>
            <div className="agent-progress">
              <span>
                Saved research progress
                <strong>
                  {job.step} / {job.limits.max_steps} steps
                </strong>
              </span>
              <progress
                value={job.step}
                max={job.limits.max_steps}
                aria-label="Completed research steps"
              />
            </div>
            <div className="agent-detail-meta">
              <span>
                {number(job.tokens_input)} input · {number(job.tokens_output)} output tokens
              </span>
              <strong>{money(job.cost_usd)}</strong>
            </div>
            {job.error && <p className="agent-reason">{job.error}</p>}
            <p className="agent-help">{policy?.help}</p>
            {policy?.allowed && (
              <form
                className="auth-form agent-resume"
                noValidate
                onSubmit={(event) => {
                  event.preventDefault();
                  if (!event.currentTarget.reportValidity()) return;
                  void resume(job, Math.min(steps, 100 - job.limits.max_steps));
                }}
              >
                {job.status === 'paused_steps' && (
                  <label>
                    Additional steps
                    <input
                      type="number"
                      min={1}
                      max={100 - job.limits.max_steps}
                      step={1}
                      value={Number.isNaN(steps) ? '' : Math.min(steps, 100 - job.limits.max_steps)}
                      onChange={(event) => setSteps(event.target.valueAsNumber)}
                      required
                      disabled={busy}
                    />
                    <small>
                      Up to {100 - job.limits.max_steps} more; new requests use the daily allowance.
                    </small>
                  </label>
                )}
                <button className="agent-button primary" disabled={busy} aria-busy={busy}>
                  <Play size={14} />
                  {job.status === 'paused_context' ? 'Compact and resume' : 'Resume analysis'}
                </button>
              </form>
            )}
            {job.request && (
              <Link
                className="agent-button"
                to={`/quests/${job.request.quest_id}?game_version=${encodeURIComponent(job.request.game_version)}&locale=${encodeURIComponent(job.request.locale)}`}
              >
                <BookOpen size={14} />
                {job.status === 'completed' ? 'Read explanation' : 'Open quest'}
              </Link>
            )}
          </div>
          <details className="agent-calls">
            <summary>
              Request history <span>{job.calls.length} requests</span>
            </summary>
            <div className="agent-table-scroll" tabIndex={0} aria-label="Request history table">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Step / kind</th>
                    <th scope="col">Status</th>
                    <th scope="col">Tokens in / out</th>
                    <th scope="col">Cost / reservation</th>
                  </tr>
                </thead>
                <tbody>
                  {job.calls.map((call) => (
                    <tr key={call.id}>
                      <td>
                        {call.kind === 'analysis' ? call.step + 1 : call.kind}
                        <small>{call.model}</small>
                      </td>
                      <td>
                        {call.status.replaceAll('_', ' ')}
                        {call.provider_error && <small>{call.provider_error.code}</small>}
                        {call.retry_at && (
                          <small>
                            Retry after {new Date(call.retry_at).toLocaleString('en-US')}
                          </small>
                        )}
                      </td>
                      <td>
                        {call.input_tokens === null
                          ? '—'
                          : `${number(call.input_tokens)} / ${number(call.output_tokens)}`}
                      </td>
                      <td>
                        {call.cost_usd === null
                          ? `${money(call.reserved_usd)} held`
                          : money(call.cost_usd)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {job.calls.length === 0 && (
              <p className="empty-inline">No provider requests recorded yet.</p>
            )}
          </details>
        </>
      )}
    </section>
  );
}
