import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { api } from '../../api/client';
import { statusLabel } from '../../data/storyAgent';

type RevisitPage = {
  revisits: {
    id: number;
    document_id: number;
    release_id: number;
    run_id: number | null;
    status: string;
    candidate_count: number;
    error: string | null;
  }[];
  next_before: number | null;
};

export function AgentRevisits({ refresh }: { refresh: number }) {
  const [params, setParams] = useSearchParams();
  const before = params.get('review_before');
  const [data, setData] = useState<RevisitPage | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void api<RevisitPage>(
      `/admin/story-agent/revisits${before ? `?before=${encodeURIComponent(before)}` : ''}`,
      { signal: controller.signal },
    )
      .then((value) => {
        if (!controller.signal.aborted) {
          setData(value);
          setError('');
        }
      })
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setError(reason.message);
      });
    return () => controller.abort();
  }, [before, refresh, retry]);
  function page(cursor: number | null) {
    setData(null);
    setParams((previous) => {
      const next = new URLSearchParams(previous);
      if (cursor) next.set('review_before', String(cursor));
      else next.delete('review_before');
      return next;
    });
  }
  return (
    <details className="content-panel agent-ledger">
      <summary>Reviews after new imports</summary>
      <p>
        Candidate searches are free. Matched threads use the same daily allowance. Failed queue
        deliveries retry automatically; paused runs keep their saved research.
      </p>
      {error && (
        <div className="agent-feedback error" role="alert">
          {error}
          <button className="agent-button" onClick={() => setRetry((value) => value + 1)}>
            Retry
          </button>
        </div>
      )}
      {!data && !error && <p role="status">Loading reviews…</p>}
      {data?.revisits.length === 0 && (
        <p>No reviews on this page. New imports schedule flagged threads automatically.</p>
      )}
      {data?.revisits.map((task) => (
        <article className="admin-alert" key={task.id}>
          <header>
            <strong>Review #{task.id}</strong>
            <span className="agent-status">{statusLabel(task.status)}</span>
          </header>
          <p>
            Explanation #{task.document_id} · snapshot #{task.release_id} · {task.candidate_count}{' '}
            candidates
          </p>
          {task.status === 'no_candidates' && (
            <p>No matching source candidates found. The thread remains unresolved.</p>
          )}
          {task.error && <p>{task.error}</p>}
          {task.run_id && (
            <Link className="agent-button" to={`/admin/story-agent?job=${task.run_id}`}>
              Inspect run #{task.run_id}
            </Link>
          )}
        </article>
      ))}
      <div className="agent-pagination">
        {before && (
          <button className="agent-button" onClick={() => page(null)}>
            Newest reviews
          </button>
        )}
        {data?.next_before && (
          <button className="agent-button" onClick={() => page(data.next_before)}>
            Older reviews
          </button>
        )}
      </div>
    </details>
  );
}
