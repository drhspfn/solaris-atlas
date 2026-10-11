import { useEffect, useState } from 'react';

import { api } from '../../api/client';
import { MediaImportReport } from './MediaImportReport';
import { QueueManager } from './QueueManager';

type Task = {
  id: number;
  processor: string;
  status: string;
  error: string | null;
  request: { kind?: string; game_version?: string; parent_id?: number; targets?: string[] };
  result: Record<string, unknown>;
  media_report_available?: boolean;
};
type Tasks = { tasks: Task[]; running?: Task[]; next_before: number | null };
type Queues = {
  available: boolean;
  error: string | null;
  queues: {
    name: string;
    ready: number;
    active: number;
    consumers: number;
    workers: { name: string; prefetch: number }[];
  }[];
};

function TaskRows({ rows }: { rows: Task[] }) {
  return (
    <>
      {rows.map((task) => (
        <tr key={task.id}>
          <th scope="row">
            #{task.id}
            {task.request.parent_id && <small>Depends on #{task.request.parent_id}</small>}
          </th>
          <td>
            {(task.request.kind || task.processor).replaceAll('_', ' ')}
            <small>
              {task.request.game_version ? `Version ${task.request.game_version}` : ''}
              {task.request.targets?.length ? ` · ${task.request.targets.length} references` : ''}
            </small>
          </td>
          <td>
            <span className={`agent-status status-${task.status}`}>
              {task.status.replaceAll('_', ' ')}
            </span>
          </td>
          <td>
            {(task.error || Object.keys(task.result).length > 0) && (
              <details className="task-details">
                <summary>{task.error ? 'View message' : 'View result'}</summary>
                {task.error && <p>{task.error}</p>}
                {Object.entries(task.result).map(([key, value]) => (
                  <small key={key}>
                    {key.replaceAll('_', ' ')}:{' '}
                    {Array.isArray(value) ? `${value.length} missing` : String(value)}
                  </small>
                ))}
              </details>
            )}
            {task.media_report_available && <MediaImportReport taskId={task.id} />}
          </td>
        </tr>
      ))}
    </>
  );
}

export function ProcessingActivity() {
  const [tasks, setTasks] = useState<Tasks | null>(null);
  const [queues, setQueues] = useState<Queues | null>(null);
  const [pages, setPages] = useState<(number | null)[]>([null]);
  const before = pages[pages.length - 1];
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') setRevision((value) => value + 1);
    }, 10000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    const params = new URLSearchParams({ limit: '15' });
    if (before) params.set('before', String(before));
    if (status) params.set('status', status);
    void Promise.all([
      api<Tasks>(`/admin/data-operations/tasks?${params}`, {
        signal: controller.signal,
        cacheTtl: 0,
      }),
      api<Queues>('/admin/data-operations/queues', { signal: controller.signal, cacheTtl: 0 }),
    ])
      .then(([runs, broker]) => {
        if (!controller.signal.aborted) {
          setTasks(runs);
          setQueues(broker);
          setError('');
        }
      })
      .catch((reason) => {
        if (!controller.signal.aborted)
          setError(
            reason instanceof Error ? reason.message : 'Could not refresh processing activity.',
          );
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [before, status, revision]);
  return (
    <>
      <section className="content-panel data-operation-panel">
        <header className="agent-panel-header">
          <div>
            <h3>Queues & workers</h3>
            <p>
              Live updates every 10 seconds. Delivered messages and executing tasks are shown
              separately.
            </p>
          </div>
        </header>
        {error && (
          <p role="alert">
            {error}{' '}
            <button className="agent-button" onClick={() => setRevision((value) => value + 1)}>
              Retry
            </button>
          </p>
        )}
        {queues?.error && <p role="status">{queues.error}</p>}
        {!queues && !error && <p role="status">Loading queue activity…</p>}
        {queues?.available && (
          <QueueManager queues={queues.queues} refresh={() => setRevision((value) => value + 1)} />
        )}
      </section>
      <section className="content-panel data-operation-panel" aria-busy={loading}>
        <header className="agent-panel-header">
          <div>
            <h3>Running now</h3>
            <p>Worker-recorded execution, independent of history pages and status filters.</p>
          </div>
        </header>
        {!!tasks?.running?.length && (
          <div className="agent-table-scroll" tabIndex={0} aria-label="Currently running tasks">
            <table>
              <thead>
                <tr>
                  <th>Task</th>
                  <th>Type / snapshot</th>
                  <th>Status</th>
                  <th>Result</th>
                </tr>
              </thead>
              <tbody>
                <TaskRows rows={tasks.running} />
              </tbody>
            </table>
          </div>
        )}
        {tasks && !tasks.running?.length && (
          <p>
            No tasks are recorded as running. Broker-delivered messages may still be waiting for a
            worker slot; this does not confirm execution.
          </p>
        )}
        {!tasks && <p>Loading current execution…</p>}
      </section>
      <section className="content-panel data-operation-panel" aria-busy={loading}>
        <header className="agent-panel-header">
          <div>
            <h3>Task history</h3>
            <p>15 tasks per page. Open details for errors, results and missing files.</p>
          </div>
          <label className="processing-status-filter">
            Status{' '}
            <select
              value={status}
              onChange={(event) => {
                setStatus(event.target.value);
                setPages([null]);
              }}
            >
              <option value="">All statuses</option>
              {[
                'queued',
                'running',
                'waiting_dependency',
                'blocked',
                'failed',
                'partial',
                'completed',
                'enqueue_failed',
                'cancelled',
              ].map((value) => (
                <option key={value} value={value}>
                  {value.replaceAll('_', ' ')}
                </option>
              ))}
            </select>
          </label>
        </header>
        <div
          className="agent-table-scroll processing-history-frame"
          tabIndex={0}
          aria-label="Task history"
        >
          <table>
            <thead>
              <tr>
                <th>Task</th>
                <th>Type / snapshot</th>
                <th>Status</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              <TaskRows rows={tasks?.tasks ?? []} />
            </tbody>
          </table>
        </div>
        {!loading && tasks && !tasks.tasks.length && <p>No tasks match this status.</p>}
        <nav className="agent-pagination" aria-label="Task history pages">
          <button
            className="agent-button"
            disabled={loading || pages.length === 1}
            onClick={() => setPages((previous) => previous.slice(0, -1))}
          >
            Newer tasks
          </button>
          <span>
            Page {pages.length}
            {loading ? ' · Updating…' : ''}
          </span>
          {before && (
            <button className="agent-button" onClick={() => setPages([null])}>
              Latest tasks
            </button>
          )}
          {tasks?.next_before && (
            <button
              className="agent-button"
              disabled={loading}
              onClick={() => setPages((previous) => [...previous, tasks.next_before])}
            >
              Older tasks
            </button>
          )}
        </nav>
      </section>
    </>
  );
}
