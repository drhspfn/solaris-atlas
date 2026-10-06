import { Activity, Database, Film, Map as MapIcon, RefreshCw, Rocket } from 'lucide-react';
import { type FormEvent, useCallback, useEffect, useState } from 'react';

import { api, ApiError } from '../api/client';
import { useAuth } from '../auth/AuthProvider';

type ImportRun = {
  id: number;
  game_version: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  records_seen: number;
  records_created: number;
  records_updated: number;
  records_failed: number;
  error: string | null;
};
type SnapshotJob = {
  id: number;
  status: string;
  started_at: string;
  finished_at: string | null;
  error: string | null;
  version: string;
  commit: string;
  repository: string;
};
type Overview = {
  releases: {
    id: number;
    sequence: number;
    game_version: string;
    resource_version: string | null;
    upstream_name: string;
    upstream_commit: string | null;
    imported_at: string;
  }[];
  imports: ImportRun[];
  snapshot_jobs: SnapshotJob[];
  maps: { game_version: string; map_count: number; asset_jobs: number; marker_count: number }[];
};

const date = (value: string | null) => (value ? new Date(value).toLocaleString() : 'In progress');

export function DataOperationsPage() {
  const { refresh: refreshAuth } = useAuth();
  const [data, setData] = useState<Overview | null>(null);
  const [version, setVersion] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [refreshKey, setRefreshKey] = useState(0);

  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const result = await api<Overview>('/admin/data-operations', { signal });
        if (!signal?.aborted) {
          setData(result);
          setError('');
        }
      } catch (reason) {
        if (!signal?.aborted) {
          setError(reason instanceof Error ? reason.message : 'Could not load data operations.');
          if (reason instanceof ApiError && [401, 403].includes(reason.status)) void refreshAuth();
        }
      } finally {
        if (!signal?.aborted) setLoading(false);
      }
    },
    [refreshAuth],
  );

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    void refresh(controller.signal);
    const timer = window.setInterval(() => void refresh(), 10_000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [refresh, refreshKey]);

  async function enqueue(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const result = await api<{ id: number; status: string; version: string; commit: string }>(
        '/admin/data-operations/snapshots',
        { method: 'POST', body: JSON.stringify({ version }) },
      );
      setNotice(
        `Patch ${result.version} queued as run #${result.id} · ${result.commit.slice(0, 12)}.`,
      );
      setVersion('');
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not queue this patch import.');
    } finally {
      setBusy(false);
    }
  }

  const active =
    data?.snapshot_jobs.filter((job) => ['queued', 'running'].includes(job.status)).length ?? 0;
  const markerTotal = data?.maps.reduce((sum, row) => sum + row.marker_count, 0) ?? 0;
  const importedRecords = data?.imports.reduce((sum, row) => sum + row.records_created, 0) ?? 0;

  return (
    <section className="agent-page data-operations">
      <header className="page-heading agent-heading">
        <div>
          <h2>Game data</h2>
          <p>Patch imports, interactive map coverage and processing activity.</p>
        </div>
        <button
          className="agent-button"
          onClick={() => setRefreshKey((n) => n + 1)}
          disabled={loading}
        >
          <RefreshCw size={14} /> Refresh
        </button>
      </header>

      {error && (
        <p className="agent-feedback" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="agent-notice" role="status">
          {notice}
        </p>
      )}

      <div className="data-metrics">
        <article className="content-panel">
          <Database size={18} />
          <span>Imported patches</span>
          <strong>{data?.releases.length ?? '—'}</strong>
        </article>
        <article className="content-panel">
          <Activity size={18} />
          <span>Active imports</span>
          <strong>{active}</strong>
        </article>
        <article className="content-panel">
          <MapIcon size={18} />
          <span>Map markers</span>
          <strong>{markerTotal.toLocaleString('en-US')}</strong>
        </article>
        <article className="content-panel">
          <Database size={18} />
          <span>Records imported</span>
          <strong>{importedRecords.toLocaleString('en-US')}</strong>
        </article>
      </div>

      <section className="content-panel data-operation-panel">
        <header className="agent-panel-header">
          <div>
            <h3>Import a patch</h3>
            <p>Build and import a pinned patch from the upstream data repository.</p>
          </div>
        </header>
        <form className="data-import-form" onSubmit={(event) => void enqueue(event)}>
          <label htmlFor="patch-version">Patch branch</label>
          <input
            id="patch-version"
            value={version}
            onChange={(event) => setVersion(event.target.value)}
            placeholder="3.7"
            pattern="[0-9]+\.[0-9]+"
            required
          />
          <button
            className="agent-button agent-button-primary"
            type="submit"
            disabled={busy || loading}
          >
            <Rocket size={14} /> {busy ? 'Queueing…' : 'Queue patch import'}
          </button>
        </form>
      </section>

      <section className="content-panel data-operation-panel">
        <header className="agent-panel-header">
          <div>
            <h3>Imported versions</h3>
            <p>
              Snapshots are cumulative records of what the upstream branch contains at its pinned
              commit.
            </p>
          </div>
        </header>
        <div className="agent-table-scroll" tabIndex={0} aria-label="Imported game versions">
          <table>
            <thead>
              <tr>
                <th>Version</th>
                <th>Resource</th>
                <th>Upstream</th>
                <th>Imported</th>
              </tr>
            </thead>
            <tbody>
              {data?.releases.map((release) => (
                <tr key={release.id}>
                  <th scope="row">{release.game_version}</th>
                  <td>{release.resource_version ?? '—'}</td>
                  <td title={release.upstream_commit ?? undefined}>
                    {release.upstream_name}
                    <small>{release.upstream_commit?.slice(0, 12) ?? 'Commit unavailable'}</small>
                  </td>
                  <td>{date(release.imported_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!loading && !data?.releases.length && (
          <p className="empty-inline">No patch snapshots have been imported.</p>
        )}
      </section>

      <section className="content-panel data-operation-panel">
        <header className="agent-panel-header">
          <div>
            <h3>Patch import runs</h3>
            <p>Queue status is recorded by the worker; details refresh every 10 seconds.</p>
          </div>
        </header>
        <div className="agent-table-scroll" tabIndex={0} aria-label="Patch import runs">
          <table>
            <thead>
              <tr>
                <th>Run</th>
                <th>Patch</th>
                <th>Status</th>
                <th>Records</th>
                <th>Started</th>
              </tr>
            </thead>
            <tbody>
              {data?.snapshot_jobs.map((job) => (
                <tr key={job.id}>
                  <th scope="row">
                    #{job.id}
                    <small title={job.commit}>{job.commit.slice(0, 12)}</small>
                  </th>
                  <td>{job.version}</td>
                  <td>
                    <span className={`agent-status status-${job.status}`}>
                      {job.status.replaceAll('_', ' ')}
                    </span>
                    {job.error && <small>{job.error}</small>}
                  </td>
                  <td>
                    {data.imports
                      .filter((item) => item.game_version === job.version)
                      .reduce((sum, item) => sum + item.records_created, 0)
                      .toLocaleString('en-US')}
                  </td>
                  <td>{date(job.finished_at ?? job.started_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!loading && !data?.snapshot_jobs.length && (
          <p className="empty-inline">No patch import jobs yet.</p>
        )}
      </section>

      <section className="content-panel data-operation-panel">
        <header className="agent-panel-header">
          <div>
            <h3>
              <Film size={16} /> Interactive maps
            </h3>
            <p>Published map tiles and placed markers by client asset version.</p>
          </div>
        </header>
        <div
          className="agent-table-scroll"
          tabIndex={0}
          aria-label="Interactive map import coverage"
        >
          <table>
            <thead>
              <tr>
                <th>Asset version</th>
                <th>Maps</th>
                <th>Asset jobs</th>
                <th>Markers</th>
              </tr>
            </thead>
            <tbody>
              {data?.maps.map((map) => (
                <tr key={map.game_version}>
                  <th scope="row">{map.game_version}</th>
                  <td>{map.map_count}</td>
                  <td>{map.asset_jobs}</td>
                  <td>{map.marker_count.toLocaleString('en-US')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="data-operations-note">
          Map extraction is not enabled in the production Linux worker image yet. It requires the
          separately verified Windows FModelCLI and CUE4Parse tools, so this panel reports published
          map data without queuing a job that cannot run.
        </p>
      </section>

      <section className="content-panel data-operation-panel">
        <header className="agent-panel-header">
          <div>
            <h3>Import history</h3>
            <p>Deterministic database import totals for recent snapshots.</p>
          </div>
        </header>
        <div className="agent-table-scroll" tabIndex={0} aria-label="Patch import statistics">
          <table>
            <thead>
              <tr>
                <th>Patch</th>
                <th>Run</th>
                <th>Status</th>
                <th>Seen</th>
                <th>Created</th>
                <th>Failed</th>
                <th>Started</th>
              </tr>
            </thead>
            <tbody>
              {data?.imports.map((run) => (
                <tr key={run.id}>
                  <th scope="row">{run.game_version}</th>
                  <td>#{run.id}</td>
                  <td>{run.status}</td>
                  <td>{run.records_seen.toLocaleString('en-US')}</td>
                  <td>{run.records_created.toLocaleString('en-US')}</td>
                  <td>{run.records_failed.toLocaleString('en-US')}</td>
                  <td>{date(run.finished_at ?? run.started_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </section>
  );
}
