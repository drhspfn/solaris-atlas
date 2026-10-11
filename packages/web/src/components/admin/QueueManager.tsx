import { useEffect, useRef, useState } from 'react';

import { api } from '../../api/client';

export type Queue = {
  name: string;
  ready: number;
  active: number;
  consumers: number;
  workers: { name: string; prefetch: number }[];
};
const labels: Record<string, string> = {
  'wuwa.release-media.v1': 'Release media',
  'wuwa.event-media.v1': 'Event artwork',
  'wuwa.entity-media.v1': 'Icons & character audio',
  'wuwa.asset-download.v1': 'Game downloads',
  'wuwa.asset-extract.v1': 'Maps & asset extraction',
  'wuwa.snapshot-build.v1': 'Patch imports',
  'wuwa.story-agent.v1': 'Story analysis',
  'wuwa.cutscene-vision.v1': 'Cutscene analysis',
};
const managed = new Set(
  Object.keys(labels).filter(
    (name) => !['wuwa.story-agent.v1', 'wuwa.cutscene-vision.v1'].includes(name),
  ),
);

export function QueueManager({ queues, refresh }: { queues: Queue[]; refresh: () => void }) {
  const [idle, setIdle] = useState(false);
  const [cleanup, setCleanup] = useState<{
    name: string;
    scope: 'waiting' | 'failed';
    count: number;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (cleanup) dialog.current?.showModal();
    else dialog.current?.close();
  }, [cleanup]);
  const count = (name: string, suffix: string) =>
    queues.find((queue) => queue.name === name + suffix)?.ready ?? 0;
  const bases = queues.filter(
    (queue) => !queue.name.endsWith('.failed') && !queue.name.endsWith('.waiting'),
  );
  const visible = bases.filter(
    (queue) =>
      idle ||
      queue.ready + queue.active + count(queue.name, '.waiting') + count(queue.name, '.failed') > 0,
  );
  async function clear() {
    if (!cleanup || busy) return;
    setBusy(true);
    setError('');
    try {
      const result = await api<{ cancelled: number; removed: number }>(
        '/admin/data-operations/queues/clear',
        {
          method: 'POST',
          body: { name: cleanup.name, scope: cleanup.scope },
        },
      );
      setNotice(
        `${labels[cleanup.name]}: ${result.cancelled} tasks cancelled, ${result.removed} messages removed. Running tasks and history kept.`,
      );
      setCleanup(null);
      refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not finish cleanup.');
      refresh();
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <div className="queue-toolbar">
        <label>
          <input type="checkbox" checked={idle} onChange={(e) => setIdle(e.target.checked)} /> Show
          idle queues
        </label>
        <span>Waiting includes tasks delayed by dependencies.</span>
      </div>
      {notice && (
        <p className="agent-notice" role="status">
          {notice}
        </p>
      )}
      <div className="agent-table-scroll" tabIndex={0} aria-label="Queue management">
        <table>
          <thead>
            <tr>
              <th>Operation</th>
              <th>Waiting</th>
              <th>Delivered</th>
              <th>Failed messages</th>
              <th>Workers</th>
              <th>Manage</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((queue) => {
              const waiting = queue.ready + count(queue.name, '.waiting');
              const failed = count(queue.name, '.failed');
              return (
                <tr key={queue.name}>
                  <th scope="row">{labels[queue.name] ?? queue.name}</th>
                  <td>{waiting}</td>
                  <td>{queue.active}</td>
                  <td>{failed}</td>
                  <td>
                    <details>
                      <summary>{queue.consumers} connected</summary>
                      {queue.workers.map((worker, index) => (
                        <small key={index}>
                          {worker.name} · {worker.prefetch} delivery slots
                        </small>
                      ))}
                      <small>{queue.name}</small>
                    </details>
                  </td>
                  <td>
                    {managed.has(queue.name) && (
                      <div className="queue-actions">
                        <button
                          className="agent-button"
                          disabled={busy || waiting === 0}
                          onClick={() => {
                            setError('');
                            setCleanup({ name: queue.name, scope: 'waiting', count: waiting });
                          }}
                        >
                          Clear waiting
                        </button>
                        <button
                          className="agent-button"
                          disabled={busy || failed === 0}
                          onClick={() => {
                            setError('');
                            setCleanup({ name: queue.name, scope: 'failed', count: failed });
                          }}
                        >
                          Clear failed
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {!visible.length && (
          <p className="empty-inline">
            Queues are idle. Enable “Show idle queues” to inspect workers.
          </p>
        )}
      </div>
      <dialog
        ref={dialog}
        className="queue-cleanup-dialog"
        aria-labelledby="cleanup-title"
        onCancel={(e) => {
          if (busy) e.preventDefault();
          else setCleanup(null);
        }}
        onClose={() => {
          if (!busy) setCleanup(null);
        }}
      >
        <h3 id="cleanup-title">Clear {cleanup?.scope} queue?</h3>
        <p>
          {cleanup ? labels[cleanup.name] : ''}: currently {cleanup?.count ?? 0} messages.
        </p>
        <p>
          {cleanup?.scope === 'waiting'
            ? 'Cancel waiting tasks and remove their queue messages. Running tasks, imported files and history are kept. Retry the import to schedule cancelled work again.'
            : 'Remove failed broker messages. Error details remain in task history. This does not retry failed tasks.'}
        </p>
        {error && (
          <p className="agent-feedback" role="alert">
            {error}
          </p>
        )}
        <div className="queue-actions">
          <button
            autoFocus
            className="agent-button"
            disabled={busy}
            onClick={() => setCleanup(null)}
          >
            Keep queue
          </button>
          <button className="agent-button" disabled={busy} onClick={() => void clear()}>
            {busy ? 'Clearing…' : 'Clear queue'}
          </button>
        </div>
      </dialog>
    </>
  );
}
