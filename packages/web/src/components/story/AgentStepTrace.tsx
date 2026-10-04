import { useState } from 'react';

import { api } from '../../api/client';

type Trace = {
  recorded: boolean;
  created_at: string;
  text: string;
  tools: {
    name: string;
    arguments: string;
    result: string;
    status: string;
    validation?: string;
    duration_ms?: number;
    repeat_count?: number;
    new_evidence?: number;
  }[];
};

export function AgentStepTrace({ runId, callId }: { runId: number; callId: number }) {
  const [trace, setTrace] = useState<Trace | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  async function load() {
    setError('');
    setLoading(true);
    try {
      setTrace(await api<Trace>(`/admin/story-agent/jobs/${runId}/calls/${callId}`));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not load this step.');
    } finally {
      setLoading(false);
    }
  }
  return (
    <details
      className="agent-step-trace"
      onToggle={(event) => {
        if (event.currentTarget.open && !trace && !loading) void load();
      }}
    >
      <summary>Step details</summary>
      {loading && <p role="status">Loading step…</p>}
      {error && (
        <p role="alert">
          {error} <button onClick={() => void load()}>Retry</button>
        </p>
      )}
      {trace && (
        <>
          <small>{new Date(trace.created_at).toLocaleString()}</small>
          {!trace.recorded && (
            <p>
              Execution results were not recorded for this request. Only available model output is
              shown.
            </p>
          )}
          {trace.text && (
            <>
              <strong>Model response</strong>
              <pre>{trace.text}</pre>
            </>
          )}
          {trace.tools.map((tool, index) => (
            <section key={index}>
              <strong>
                {tool.name} · {tool.status}
              </strong>
              {tool.repeat_count !== undefined && (
                <small>
                  {tool.duration_ms} ms · {tool.new_evidence} new evidence entries
                  {tool.repeat_count > 0 && ` · repeated request (${tool.repeat_count} previous)`}
                </small>
              )}
              {tool.validation && <p role="status">Validation: {tool.validation}</p>}
              <details>
                <summary>Arguments</summary>
                <pre>{tool.arguments}</pre>
              </details>
              <details>
                <summary>Result</summary>
                <pre>{tool.result}</pre>
              </details>
            </section>
          ))}
          {!trace.text && trace.tools.length === 0 && (
            <p>No visible model text or tool calls recorded.</p>
          )}
        </>
      )}
    </details>
  );
}
