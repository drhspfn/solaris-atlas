import {
  Activity,
  AlertCircle,
  CalendarDays,
  CheckCircle,
  Database,
  Download,
  HardDrive,
  Map as MapIcon,
  RefreshCw,
  Rocket,
} from 'lucide-react';
import { type FormEvent, useCallback, useEffect, useState } from 'react';

import { api, ApiError } from '../api/client';
import { useAuth } from '../auth/AuthProvider';
import { ProcessingActivity } from '../components/admin/ProcessingActivity';

type ImportRun = {
  id: number;
  release_id: number;
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

type ProcessingJob = {
  id: number;
  status: string;
  started_at: string;
  finished_at: string | null;
  error: string | null;
  version?: string;
  commit?: string;
  repository?: string;
  tier?: string;
  download_id?: string;
  file_count?: number;
  size_bytes?: number;
  result?: {
    import_run_id?: number;
    records_seen?: number;
    records_created?: number;
    records_failed?: number;
  } | null;
};

type InstalledClient = {
  version: string;
  tier: string;
  download_id: string;
  keys_commit?: string;
  state: string;
  key_count: number;
  file_count: number;
  size_bytes: number;
  path?: string;
};

type ClientAssetsOverview = {
  live_version: string | null;
  installed: InstalledClient[];
  active_client: InstalledClient | null;
  active_download: {
    id: number;
    status: string;
    started_at: string;
    version?: string;
    tier?: string;
    download_id?: string;
    file_count?: number;
    size_bytes?: number;
  } | null;
};

type Overview = {
  active_tasks: number;
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
  snapshot_jobs: ProcessingJob[];
  asset_jobs?: ProcessingJob[];
  map_jobs?: ProcessingJob[];
  maps: { game_version: string; map_count: number; asset_jobs: number; marker_count: number }[];
  client_assets?: ClientAssetsOverview;
};

const date = (value: string | null) => (value ? new Date(value).toLocaleString() : 'In progress');
const formatGiB = (bytes: number) => `${(bytes / 1024 ** 3).toFixed(1)} GiB`;

export function DataOperationsPage() {
  const { refresh: refreshAuth } = useAuth();
  const [data, setData] = useState<Overview | null>(null);
  const [patchVersion, setPatchVersion] = useState('');
  const [clientVersion, setClientVersion] = useState('');
  const [clientTier, setClientTier] = useState<'sd' | 'hd' | 'uhd'>('hd');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [refreshKey, setRefreshKey] = useState(0);
  const [view, setView] = useState('activity');

  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const result = await api<Overview>('/admin/data-operations', { signal, cacheTtl: 0 });
        if (!signal?.aborted) {
          setData(result);
          setError('');
          if (!clientVersion && result.client_assets?.live_version) {
            setClientVersion(result.client_assets.live_version);
          }
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
    [refreshAuth, clientVersion],
  );

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    void refresh(controller.signal);
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void refresh(controller.signal);
    }, 10_000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [refresh, refreshKey]);

  async function enqueuePatch(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const result = await api<{ id: number; status: string; version: string; commit: string }>(
        '/admin/data-operations/snapshots',
        { method: 'POST', body: { version: patchVersion } },
      );
      setNotice(
        `Patch ${result.version} queued as run #${result.id} · ${result.commit.slice(0, 12)}.`,
      );
      setPatchVersion('');
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not queue this patch import.');
    } finally {
      setBusy(false);
    }
  }

  async function enqueueClientDownload(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const result = await api<{
        id: number;
        status: string;
        version: string;
        tier: string;
        download_id: string;
        file_count: number;
        size_bytes: number;
      }>('/admin/data-operations/client-download', {
        method: 'POST',
        body: {
          version: clientVersion.trim() || undefined,
          tier: clientTier,
        },
      });
      setNotice(
        `Client ${result.version} (${result.tier.toUpperCase()}) download queued as run #${result.id} · ${result.file_count} files (${formatGiB(result.size_bytes)}).`,
      );
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not queue client download.');
    } finally {
      setBusy(false);
    }
  }

  async function enqueueMapBuild() {
    setBusy(true);
    setError('');
    setNotice('');
    const target = data?.client_assets?.active_client;
    try {
      const result = await api<{
        id: number;
        status: string;
        version: string;
        tier: string;
        download_id: string;
      }>('/admin/data-operations/maps/build', {
        method: 'POST',
        body: {
          version: target?.version ?? '3.7.0',
          tier: target?.tier ?? 'hd',
          download_id: target?.download_id,
        },
      });
      setNotice(`Interactive map build for ${result.version} queued as run #${result.id}.`);
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not queue interactive map build.');
    } finally {
      setBusy(false);
    }
  }

  async function importEventArchive() {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const result = await api<{
        events: number;
        occurrences: number;
        source_revision: string;
      }>('/admin/data-operations/events/import', { method: 'POST' });
      setNotice(
        `Event archive refreshed: ${result.events} events and ${result.occurrences} dated schedules.`,
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not import the event archive.');
    } finally {
      setBusy(false);
    }
  }

  async function enqueueMedia(releaseId: number) {
    setBusy(true);
    setError('');
    try {
      const result = await api<{ tasks: number; queued: number; enqueue_failed: number }>(
        `/admin/data-operations/releases/${releaseId}/media`,
        { method: 'POST' },
      );
      setNotice(
        `Media import: ${result.tasks} tasks, ${result.queued} queued, ${result.enqueue_failed} queue failures.`,
      );
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not queue media.');
    } finally {
      setBusy(false);
    }
  }

  const activeSnapshots =
    data?.snapshot_jobs.filter((job) => ['queued', 'running'].includes(job.status)).length ?? 0;
  const activeAssetJobs =
    data?.asset_jobs?.filter((job) => ['queued', 'running'].includes(job.status)).length ?? 0;
  const activeMapJobs =
    data?.map_jobs?.filter((job) => ['queued', 'running'].includes(job.status)).length ?? 0;
  const totalActive = data?.active_tasks ?? activeSnapshots + activeAssetJobs + activeMapJobs;

  const markerTotal = data?.maps.reduce((sum, row) => sum + row.marker_count, 0) ?? 0;
  const importedRecords = data?.imports.reduce((sum, row) => sum + row.records_created, 0) ?? 0;

  const activeClient = data?.client_assets?.active_client;
  const activeDownload = data?.client_assets?.active_download;

  return (
    <section className="agent-page data-operations">
      <header className="page-heading agent-heading">
        <div>
          <h2>Game data & operations</h2>
          <p>
            Game client downloads, patch imports, interactive map extraction and processing
            activity.
          </p>
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
          <HardDrive size={18} />
          <span>Game client</span>
          <strong>
            {activeDownload
              ? 'Downloading…'
              : activeClient
                ? `${activeClient.version} ${activeClient.tier.toUpperCase()}`
                : 'Not downloaded'}
          </strong>
        </article>
        <article className="content-panel">
          <Activity size={18} />
          <span>Unfinished tasks</span>
          <strong>{totalActive}</strong>
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

      <nav className="operations-views" aria-label="Data operations views">
        {['activity', 'imports', 'history'].map((value) => (
          <button
            key={value}
            className="agent-button"
            aria-pressed={view === value}
            onClick={() => setView(value)}
          >
            {value === 'activity'
              ? 'Queues & tasks'
              : value === 'imports'
                ? 'Run imports'
                : 'Import history'}
          </button>
        ))}
      </nav>
      <div className="operations-view" hidden={view !== 'activity'}>
        <ProcessingActivity />
      </div>
      <div className="operations-view" hidden={view !== 'imports'}>
        <section className="content-panel data-operation-panel">
          <header className="agent-panel-header">
            <div>
              <h3>
                <CalendarDays size={16} /> Historical event archive
              </h3>
              <p>
                Import dated convenes, limited events and recurring or permanent modes from the
                versioned game-data archive.
              </p>
            </div>
            <button
              className="agent-button"
              onClick={() => void importEventArchive()}
              disabled={busy}
            >
              <Download size={14} /> {busy ? 'Importing…' : 'Import event history'}
            </button>
          </header>
        </section>

        {/* SECTION: Game client asset downloader */}
        <section className="content-panel data-operation-panel">
          <header className="agent-panel-header">
            <div>
              <h3>
                <HardDrive size={16} /> Official game client
              </h3>
              <p>
                Download PAK archives and AES keys from the official Kuro launcher CDN. The game
                client provides raw textures, icons, cutscene MP4s and audio banks for map and media
                extractors.
              </p>
            </div>
          </header>

          <div className="client-asset-banner">
            <div className="client-asset-info">
              {activeDownload ? (
                <span className="agent-status active">
                  <Activity size={14} /> Downloading run #{activeDownload.id}
                </span>
              ) : activeClient ? (
                <span className="agent-status done">
                  <CheckCircle size={14} /> Client ready ({activeClient.version})
                </span>
              ) : (
                <span className="agent-status paused">
                  <AlertCircle size={14} /> Client not downloaded
                </span>
              )}
              <div className="client-asset-details">
                <span>
                  Live launcher: <strong>{data?.client_assets?.live_version ?? '3.7.0'}</strong>
                </span>
                {activeClient && (
                  <>
                    <span>
                      Tier: <strong>{activeClient.tier.toUpperCase()}</strong>
                    </span>
                    <span>
                      Files: <strong>{activeClient.file_count}</strong>
                    </span>
                    <span>
                      Size: <strong>{formatGiB(activeClient.size_bytes)}</strong>
                    </span>
                    {activeClient.key_count > 0 && (
                      <span>
                        AES keys: <strong>{activeClient.key_count} dynamic</strong>
                      </span>
                    )}
                    <span title={activeClient.download_id}>
                      Download ID: <code>{activeClient.download_id.slice(0, 16)}…</code>
                    </span>
                  </>
                )}
              </div>
            </div>
          </div>

          <form
            className="data-import-form"
            onSubmit={(event) => void enqueueClientDownload(event)}
          >
            <label htmlFor="client-version">
              Version
              <input
                id="client-version"
                value={clientVersion}
                onChange={(event) => setClientVersion(event.target.value)}
                placeholder={data?.client_assets?.live_version ?? '3.7.0'}
                pattern="[0-9]+\.[0-9]+(\.[0-9]+)?"
              />
            </label>
            <label htmlFor="client-tier">
              Resource tier
              <select
                id="client-tier"
                value={clientTier}
                onChange={(e) => setClientTier(e.target.value as 'sd' | 'hd' | 'uhd')}
              >
                <option value="hd">HD (Default)</option>
                <option value="sd">SD</option>
                <option value="uhd">UHD</option>
              </select>
            </label>
            <button
              className="agent-button agent-button-primary"
              type="submit"
              disabled={busy || loading || Boolean(activeDownload)}
            >
              <Download size={14} />{' '}
              {busy
                ? 'Queueing…'
                : activeDownload
                  ? 'Download in progress'
                  : 'Download game client'}
            </button>
          </form>

          {Boolean(data?.asset_jobs?.length) && (
            <div className="agent-table-scroll" tabIndex={0} aria-label="Game client download runs">
              <table>
                <thead>
                  <tr>
                    <th>Run</th>
                    <th>Version</th>
                    <th>Tier</th>
                    <th>Status</th>
                    <th>Files / Size</th>
                    <th>Started</th>
                  </tr>
                </thead>
                <tbody>
                  {data?.asset_jobs?.map((job) => (
                    <tr key={job.id}>
                      <th scope="row">
                        #{job.id}
                        {job.download_id && (
                          <small title={job.download_id}>{job.download_id.slice(0, 12)}</small>
                        )}
                      </th>
                      <td>{job.version ?? '—'}</td>
                      <td>{job.tier?.toUpperCase() ?? '—'}</td>
                      <td>
                        <span className={`agent-status status-${job.status}`}>
                          {job.status.replaceAll('_', ' ')}
                        </span>
                        {job.error && <small>{job.error}</small>}
                      </td>
                      <td>
                        {job.file_count
                          ? `${job.file_count} files (${formatGiB(job.size_bytes ?? 0)})`
                          : '—'}
                      </td>
                      <td>{date(job.finished_at ?? job.started_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* SECTION: Interactive maps */}
        <section className="content-panel data-operation-panel">
          <header className="agent-panel-header">
            <div>
              <h3>
                <MapIcon size={16} /> Interactive maps
              </h3>
              <p>
                Extract map tile textures, coordinate grids and placement markers from downloaded
                game client archives.
              </p>
            </div>
          </header>

          <div className="client-asset-banner">
            <div className="client-asset-info">
              <button
                className="agent-button agent-button-primary"
                onClick={() => void enqueueMapBuild()}
                disabled={busy || loading || !activeClient || activeMapJobs > 0}
              >
                <MapIcon size={14} />{' '}
                {activeMapJobs > 0
                  ? 'Map extraction in progress…'
                  : activeClient
                    ? `Build maps for ${activeClient.version}`
                    : 'Requires downloaded client'}
              </button>
              <span className="data-operations-note" style={{ margin: 0 }}>
                {activeClient
                  ? `Ready using client build ${activeClient.download_id.slice(0, 12)}…`
                  : 'Download the game client archives above before queueing map builds.'}
              </span>
            </div>
          </div>

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
          {!loading && !data?.maps.length && (
            <p className="empty-inline">No interactive maps have been built yet.</p>
          )}

          {Boolean(data?.map_jobs?.length) && (
            <div
              className="agent-table-scroll"
              tabIndex={0}
              aria-label="Interactive map build runs"
            >
              <table>
                <thead>
                  <tr>
                    <th>Run</th>
                    <th>Version</th>
                    <th>Status</th>
                    <th>Started</th>
                  </tr>
                </thead>
                <tbody>
                  {data?.map_jobs?.map((job) => (
                    <tr key={job.id}>
                      <th scope="row">#{job.id}</th>
                      <td>{job.version ?? '—'}</td>
                      <td>
                        <span className={`agent-status status-${job.status}`}>
                          {job.status.replaceAll('_', ' ')}
                        </span>
                        {job.error && <small>{job.error}</small>}
                      </td>
                      <td>{date(job.finished_at ?? job.started_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* SECTION: Patch snapshots import */}
        <section className="content-panel data-operation-panel">
          <header className="agent-panel-header">
            <div>
              <h3>
                <Rocket size={16} /> Import a patch
              </h3>
              <p>
                Build and import a pinned patch from the upstream data repository (1.0, 1.1, etc.).
                Snapshots contain deterministic entity tables, quest lines, dialogue trees and
                localization.
              </p>
            </div>
          </header>
          <form className="data-import-form" onSubmit={(event) => void enqueuePatch(event)}>
            <label htmlFor="patch-version">
              Patch branch
              <input
                id="patch-version"
                value={patchVersion}
                onChange={(event) => setPatchVersion(event.target.value)}
                placeholder="3.7"
                pattern="[0-9]+\.[0-9]+"
                required
              />
            </label>
            <button
              className="agent-button agent-button-primary"
              type="submit"
              disabled={busy || loading}
            >
              <Rocket size={14} /> {busy ? 'Queueing…' : 'Queue patch import'}
            </button>
          </form>
        </section>

        {/* SECTION: Imported versions */}
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
                  <th>Media</th>
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
                    <td>
                      <button
                        type="button"
                        className="agent-button"
                        disabled={busy}
                        onClick={() => void enqueueMedia(release.id)}
                      >
                        Import / retry media
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!loading && !data?.releases.length && (
            <p className="empty-inline">No patch snapshots have been imported.</p>
          )}
        </section>
      </div>
      <div className="operations-view" hidden={view !== 'history'}>
        {/* SECTION: Patch import runs */}
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
                      {job.commit && <small title={job.commit}>{job.commit.slice(0, 12)}</small>}
                    </th>
                    <td>{job.version}</td>
                    <td>
                      <span className={`agent-status status-${job.status}`}>
                        {job.status.replaceAll('_', ' ')}
                      </span>
                      {job.error && <small>{job.error}</small>}
                    </td>
                    <td>
                      {(() => {
                        const release = data.releases.find(
                          (item) => item.upstream_commit === job.commit,
                        );
                        const imported = data.imports.find((item) =>
                          job.result?.import_run_id
                            ? item.id === job.result.import_run_id
                            : item.release_id === release?.id,
                        );
                        const seen = job.result?.records_seen ?? imported?.records_seen;
                        return seen === undefined
                          ? 'Statistics unavailable'
                          : seen.toLocaleString('en-US');
                      })()}
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

        {/* SECTION: Import history */}
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
      </div>
    </section>
  );
}
