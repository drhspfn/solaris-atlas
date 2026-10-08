import '../styles/story-agent.css';

import { Film, RefreshCw, X } from 'lucide-react';
import { type FormEvent, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { api, ApiError } from '../api/client';
import { useAuth } from '../auth/AuthProvider';
import { FlowPlayer } from '../components/story/QuestCutscenes';
import { type CutsceneFlow } from '../components/story/QuestMediaReferences';
import { APP_SETTINGS } from '../config/settings';
import { statusLabel } from '../data/storyAgent';

type Asset = { id: number; reference: string; game_version: string };
type Job = {
  id: number;
  reference: string;
  asset_node_id: number;
  game_version: string;
  status: string;
  step: number;
  frames_done: number;
  frames_remaining: number;
  error: string | null;
  cost_usd: number | null;
  tokens_input: number | null;
  tokens_output: number | null;
  output_tokens: number | null;
};
type Assets = { enabled: boolean; assets: Asset[]; next_before: number | null };
type Jobs = { jobs: Job[]; next_before: number | null };
const resumable = new Set([
  'paused_budget',
  'paused_output',
  'paused_validation',
  'paused_provider',
  'paused_config',
  'paused_input',
]);

function CutscenePreview({ asset }: { asset: Asset }) {
  const [open, setOpen] = useState(false);
  const [flow, setFlow] = useState<CutsceneFlow | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    void api<CutsceneFlow>(
      `/admin/story-agent/cutscenes/assets/${asset.id}/playback?game_version=${encodeURIComponent(asset.game_version)}`,
      { signal: controller.signal },
    )
      .then((value) => {
        if (!controller.signal.aborted) {
          setFlow(value);
          setError('');
        }
      })
      .catch((reason) => {
        if (!controller.signal.aborted)
          setError(reason instanceof Error ? reason.message : 'Could not load video.');
      });
    return () => controller.abort();
  }, [open, asset.id, asset.game_version, retry]);
  return (
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>Watch cutscene</summary>
      {open &&
        (error ? (
          <p role="alert">
            {error}{' '}
            <button
              type="button"
              className="agent-button"
              onClick={() => setRetry((value) => value + 1)}
            >
              Retry
            </button>
          </p>
        ) : flow ? (
          <FlowPlayer flow={flow} title={`Cutscene · ${asset.reference.split('/').at(-1)}`} />
        ) : (
          <p role="status">Loading video…</p>
        ))}
    </details>
  );
}

export function CutsceneAnalysisPage() {
  const { refresh: refreshAuth } = useAuth();
  const [params, setParams] = useSearchParams();
  const selectedJob = params.get('job');
  const [assets, setAssets] = useState<Assets | null>(null);
  const [jobs, setJobs] = useState<Jobs | null>(null);
  const query = params.get('q') || '';
  const [draft, setDraft] = useState(query);
  const searchInput = useRef<HTMLInputElement>(null);
  const [assetBefore, setAssetBefore] = useState<number | null>(null);
  const [jobBefore, setJobBefore] = useState<number | null>(null);
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [actionError, setActionError] = useState('');
  const [notice, setNotice] = useState('');
  const mutation = useRef(false);
  const active = useRef<AbortController | null>(null);
  useEffect(() => setDraft(query), [query]);
  useEffect(() => () => active.current?.abort(), []);
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (!mutation.current && document.visibilityState === 'visible')
        setRevision((value) => value + 1);
    }, APP_SETTINGS.storyAgent.refreshMs);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    const request = new AbortController();
    setLoading(true);
    const base = '/admin/story-agent/cutscenes';
    void Promise.all([
      api<Assets>(
        `${base}/assets?q=${encodeURIComponent(query)}${assetBefore ? `&before=${assetBefore}` : ''}`,
        { signal: request.signal },
      ),
      api<Jobs>(
        `${base}/jobs?${new URLSearchParams({ ...(jobBefore ? { before: String(jobBefore) } : {}), ...(selectedJob ? { run_id: selectedJob } : {}) })}`,
        {
          signal: request.signal,
        },
      ),
    ])
      .then(([videos, runs]) => {
        if (!request.signal.aborted) {
          setAssets(videos);
          setJobs(runs);
          setError('');
        }
      })
      .catch((e) => {
        if (!request.signal.aborted) {
          setError(e instanceof Error ? e.message : 'Unable to load analysis tasks.');
          if (e instanceof ApiError && [401, 403].includes(e.status)) void refreshAuth();
        }
      })
      .finally(() => {
        if (!request.signal.aborted) setLoading(false);
      });
    return () => request.abort();
  }, [query, assetBefore, jobBefore, revision, refreshAuth, selectedJob]);

  async function mutate(path: string, body: unknown) {
    if (mutation.current) return;
    mutation.current = true;
    setBusy(true);
    setActionError('');
    setNotice('');
    const request = new AbortController();
    active.current = request;
    try {
      const result = await api<{ id?: number; jobs?: Job[] }>(path, {
        method: 'POST',
        body,
        signal: request.signal,
      });
      if (!request.signal.aborted) {
        const id = result.id ?? result.jobs?.[0]?.id;
        setNotice(`Run #${id} saved. Matching requests reuse existing tasks.`);
        setJobBefore(null);
        setParams((previous) => {
          const next = new URLSearchParams(previous);
          next.set('job', String(id));
          return next;
        });
        setRevision((value) => value + 1);
      }
    } catch (e) {
      if (!request.signal.aborted) {
        setActionError(e instanceof Error ? e.message : 'Action failed. Try again.');
        if (e instanceof ApiError && [401, 403].includes(e.status)) void refreshAuth();
      }
    } finally {
      mutation.current = false;
      if (!request.signal.aborted) setBusy(false);
    }
  }
  function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAssetBefore(null);
    setParams((previous) => {
      const next = new URLSearchParams(previous);
      next.set('q', draft.trim());
      return next;
    });
  }
  return (
    <section className="agent-page cutscene-analysis">
      <header className="agent-heading">
        <div>
          <h2>
            <Film size={18} aria-hidden="true" /> Cutscene analysis
          </h2>
          <p>Analyze imported videos and inspect saved visual research.</p>
        </div>
        <button
          className="agent-button"
          type="button"
          disabled={busy || loading}
          onClick={() => setRevision((value) => value + 1)}
        >
          <RefreshCw size={16} aria-hidden="true" /> Refresh
        </button>
      </header>
      <p>
        Uses the same daily budget as the story agent. Tasks refresh every 10 seconds while this
        page is visible.
      </p>
      {assets && !assets.enabled && (
        <p role="status">
          Visual analysis is disabled. Set AGENT_VISION_ENABLED=true on the API and visual worker,
          then restart both services.
        </p>
      )}
      {error && (
        <p role="alert">
          {error}{' '}
          <button
            type="button"
            className="agent-button"
            onClick={() => setRevision((value) => value + 1)}
          >
            Retry
          </button>
        </p>
      )}
      {actionError && <p role="alert">{actionError}</p>}
      {notice && <p role="status">{notice}</p>}
      {loading && !jobs && <p role="status">Loading videos and analysis tasks…</p>}
      <h3>Imported videos</h3>
      <form onSubmit={search} className="cutscene-search" noValidate>
        <div className="cutscene-search-field">
          <label htmlFor="cutscene-video-search">Video name</label>
          <span className="cutscene-search-input">
            <input
              id="cutscene-video-search"
              ref={searchInput}
              name="search"
              type="search"
              placeholder="M0389"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
            />
            {draft && (
              <button
                type="button"
                className="search-clear"
                aria-label="Clear video search"
                onClick={() => {
                  setDraft('');
                  setAssetBefore(null);
                  setParams((previous) => {
                    const next = new URLSearchParams(previous);
                    next.delete('q');
                    return next;
                  });
                  searchInput.current?.focus();
                }}
              >
                <X size={16} aria-hidden="true" />
              </button>
            )}
          </span>
        </div>
        <button className="agent-button" type="submit" disabled={busy}>
          Search
        </button>
        {query && assets?.assets.length === 0 && (
          <button
            className="agent-button"
            type="button"
            onClick={() => {
              setDraft('');
              setParams((previous) => {
                const next = new URLSearchParams(previous);
                next.delete('q');
                return next;
              });
              searchInput.current?.focus();
              setAssetBefore(null);
            }}
          >
            Clear search
          </button>
        )}
      </form>
      <div aria-busy={loading} className="cutscene-job-list">
        {assets?.assets.map((asset) => (
          <article className="cutscene-job" key={`${asset.id}-${asset.game_version}`}>
            <div>
              <strong>{asset.reference}</strong>
              <small>
                Video version {asset.game_version} · Variant #{asset.id}
              </small>
            </div>
            <button
              className="agent-button"
              type="button"
              disabled={busy || loading || !assets.enabled}
              onClick={() =>
                void mutate('/admin/story-agent/cutscenes/jobs', {
                  asset_node_ids: [asset.id],
                  game_version: asset.game_version,
                })
              }
            >
              Analyze cutscene
            </button>
            <CutscenePreview asset={asset} />
          </article>
        ))}
        {assets && !loading && !assets.assets.length && (
          <p>
            {query
              ? 'No matching imported videos. Try another name.'
              : 'No imported videos yet. Import a cutscene before analyzing it.'}
          </p>
        )}
      </div>
      <div className="cutscene-pagination">
        {assetBefore && (
          <button
            className="agent-button"
            disabled={busy || loading}
            onClick={() => setAssetBefore(null)}
          >
            Latest videos
          </button>
        )}
        {assets?.next_before && (
          <button
            className="agent-button"
            disabled={busy || loading}
            onClick={() => setAssetBefore(assets.next_before)}
          >
            More videos
          </button>
        )}
      </div>
      <h3>Analysis tasks</h3>
      {selectedJob && (
        <button
          type="button"
          className="agent-button"
          onClick={() => {
            setParams({});
            setJobBefore(null);
          }}
        >
          All analysis tasks
        </button>
      )}
      <div aria-busy={loading} className="cutscene-job-list">
        {jobs?.jobs.map((job) => (
          <article
            className="cutscene-job"
            id={`visual-job-${job.id}`}
            key={job.id}
            data-selected={params.get('job') === String(job.id) || undefined}
          >
            <div>
              <strong>
                #{job.id} · {job.reference || `Variant ${job.asset_node_id}`}
              </strong>
              <span className="agent-status">{statusLabel(job.status)}</span>
              <small>
                Video version {job.game_version} · {job.step} requests · {job.frames_done} frames
                analyzed{job.frames_remaining > 0 && ` · ${job.frames_remaining} remaining`}
              </small>
              <small>
                {(job.tokens_input || 0).toLocaleString('en-US')} input ·{' '}
                {(job.tokens_output || 0).toLocaleString('en-US')} output tokens · $
                {(job.cost_usd || 0).toFixed(4)}
              </small>
              {job.frames_done + job.frames_remaining > 0 && (
                <progress
                  aria-label={`Visual analysis progress for run ${job.id}`}
                  value={job.frames_done}
                  max={job.frames_done + job.frames_remaining}
                />
              )}
              {job.error && <p>{job.error}</p>}
              {job.status === 'completed' && (
                <small>
                  Visual evidence is ready. Analyze the quest to generate its reader description.
                </small>
              )}
              {job.status === 'paused_uncertain' && (
                <small>Billing must be reconciled before retrying this task.</small>
              )}
            </div>
            {resumable.has(job.status) && (
              <button
                className="agent-button"
                type="button"
                disabled={
                  busy ||
                  !assets?.enabled ||
                  (job.status === 'paused_output' && (job.output_tokens || 0) >= 16000)
                }
                onClick={() =>
                  void mutate(
                    `/admin/story-agent/cutscenes/jobs/${job.id}/resume`,
                    job.status === 'paused_output'
                      ? {
                          output_tokens: Math.min(
                            16000,
                            Math.max(8192, (job.output_tokens || 4096) * 2),
                          ),
                        }
                      : {},
                  )
                }
              >
                {job.status === 'paused_output'
                  ? 'Increase output and continue'
                  : 'Continue analysis'}
              </button>
            )}
          </article>
        ))}
        {jobs && !loading && !jobs.jobs.length && (
          <p>No visual analysis tasks yet. Choose an imported video above.</p>
        )}
      </div>
      <div className="cutscene-pagination">
        {jobBefore && (
          <button
            className="agent-button"
            disabled={busy || loading}
            onClick={() => setJobBefore(null)}
          >
            Latest tasks
          </button>
        )}
        {jobs?.next_before && (
          <button
            className="agent-button"
            disabled={busy || loading}
            onClick={() => setJobBefore(jobs.next_before)}
          >
            Older tasks
          </button>
        )}
      </div>
    </section>
  );
}
