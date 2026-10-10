import { useEffect, useState } from 'react';

import { api, apiUrl } from '../../api/client';

type Report = {
  summary: {
    game_version?: string;
    asset_version?: string;
    requested?: number;
    found?: number;
    missing?: number;
    indexed_files?: number | null;
    exported_files?: number;
    scope?: string;
  };
  legacy: boolean;
  inventory_available: boolean;
  entries: {
    expected: string;
    status: string;
    matches: string[];
    nearby?: string[];
    config_present?: boolean | null;
    language?: string;
  }[];
  total: number;
  next_offset: number | null;
};

const reasons: Record<string, string> = {
  found: 'File found',
  export_missing: 'Present in archive; missing from export',
  not_in_archives: 'Absent from the indexed audio archives',
  not_in_export: 'Not found in export; archive index unavailable',
  not_resolved: 'No playable file resolved',
};

export function MediaImportReport({ taskId }: { taskId: number }) {
  const [open, setOpen] = useState(false);
  const [report, setReport] = useState<Report | null>(null);
  const [offset, setOffset] = useState(0);
  const [missingOnly, setMissingOnly] = useState(true);
  const [search, setSearch] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setLoading(true);
    const timer = window.setTimeout(() => {
      const params = new URLSearchParams({
        offset: String(offset),
        missing_only: String(missingOnly),
        search,
      });
      void api<Report>(`/admin/data-operations/tasks/${taskId}/media-report?${params}`, {
        signal: controller.signal,
      })
        .then((value) => {
          if (!controller.signal.aborted) {
            setReport(value);
            setError('');
          }
        })
        .catch((reason) => {
          if (!controller.signal.aborted)
            setError(reason instanceof Error ? reason.message : 'Could not load import report.');
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }, 200);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [open, taskId, offset, missingOnly, search, revision]);
  return (
    <details
      className="media-import-report"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>Import report</summary>
      {open && (
        <div aria-busy={loading}>
          <div className="data-import-form">
            <label>
              Filename
              <input
                type="search"
                value={search}
                onChange={(event) => {
                  setSearch(event.target.value);
                  setOffset(0);
                }}
              />
            </label>
            <label>
              Show
              <select
                value={missingOnly ? 'missing' : 'all'}
                onChange={(event) => {
                  setMissingOnly(event.target.value === 'missing');
                  setOffset(0);
                }}
              >
                <option value="missing">Missing files</option>
                <option value="all">All requested files</option>
              </select>
            </label>
          </div>
          {error && (
            <p role="alert">
              {error}{' '}
              <button className="agent-button" onClick={() => setRevision((value) => value + 1)}>
                Retry
              </button>
            </p>
          )}
          {loading && <p role="status">Loading report…</p>}
          {report && !loading && !error && (
            <>
              {report.summary.asset_version && (
                <p>
                  Story {report.summary.game_version ?? '—'} · Client assets{' '}
                  {report.summary.asset_version}
                </p>
              )}
              {report.summary.requested !== undefined && (
                <p>
                  {report.summary.found} found / {report.summary.requested} requested ·{' '}
                  {report.summary.missing} missing
                </p>
              )}
              {report.summary.exported_files !== undefined && (
                <p>
                  {report.summary.scope}:{' '}
                  {report.summary.indexed_files !== null &&
                  report.summary.indexed_files !== undefined
                    ? `${report.summary.indexed_files} indexed · `
                    : ''}
                  {report.summary.exported_files} exported
                </p>
              )}
              {report.legacy && (
                <p>
                  This older task recorded missing names only. Retry the media import to collect a
                  full lookup report.
                </p>
              )}
              {report.inventory_available && (
                <a
                  className="agent-button"
                  href={apiUrl(`/admin/data-operations/tasks/${taskId}/media-inventory`)}
                >
                  Download file inventory (JSONL)
                </a>
              )}
              <p>Nearby filenames are diagnostic suggestions, never automatic replacements.</p>
              {!report.entries.length && <p>No files match this filter.</p>}
              <ul className="media-report-files">
                {report.entries.map((entry, index) => (
                  <li key={`${entry.expected}-${entry.language}-${index}`}>
                    <strong>{entry.expected}</strong>
                    <small>
                      {entry.language} {reasons[entry.status] ?? entry.status}
                    </small>
                    {entry.config_present === false && (
                      <small>
                        The filename ID is also absent from the current client configuration.
                      </small>
                    )}
                    {entry.config_present === true && entry.status !== 'found' && (
                      <small>The filename ID is present in the current client configuration.</small>
                    )}
                    {entry.matches.map((path) => (
                      <small key={path}>Matched: {path}</small>
                    ))}
                    {!!entry.nearby?.length && (
                      <details>
                        <summary>Nearby filenames</summary>
                        {entry.nearby.map((path) => (
                          <small key={path}>{path}</small>
                        ))}
                      </details>
                    )}
                  </li>
                ))}
              </ul>
              <div className="media-report-pagination">
                <button
                  className="agent-button"
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - 50))}
                >
                  Previous
                </button>
                <span>
                  {report.total
                    ? `${offset + 1}–${offset + report.entries.length} of ${report.total}`
                    : '0 files'}
                </span>
                <button
                  className="agent-button"
                  disabled={report.next_offset === null}
                  onClick={() => setOffset(report.next_offset ?? 0)}
                >
                  Next
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </details>
  );
}
