// Local browser-test API. No game DB, credentials, queue or paid provider calls.
import { createServer } from 'node:http';
import { adaptiveExplanation } from './fixtures/storyExplanation.mjs';

const request = { quest_id: 139000025, game_version: '1.0.0', locale: 'en' };
const jobs = [
  {
    id: 14,
    status: 'paused_steps',
    step: 16,
    max_steps: 16,
    error: 'Step limit reached; an administrator may extend the limit and resume',
  },
  { id: 12, status: 'completed', step: 29, max_steps: 32, error: null },
  {
    id: 11,
    status: 'paused_uncertain',
    step: 13,
    max_steps: 16,
    error: 'Remote request failed; reservation retained, no automatic retry',
  },
].map((job) => ({
  ...job,
  request,
  model: 'gpt-6-luna',
  cost_usd: 0.09216682,
  tokens_input: 67110,
  tokens_output: 2983,
}));
let mode = 'admin';
let resolved = false;
const send = (res, status, data) => {
  res.writeHead(status, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(data));
};
createServer(async (req, res) => {
  res.setHeader('Access-Control-Allow-Origin', 'http://localhost:5174');
  res.setHeader('Access-Control-Allow-Credentials', 'true');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type,X-CSRF-Token');
  res.setHeader('Access-Control-Allow-Methods', 'GET,POST,OPTIONS');
  res.setHeader('Cache-Control', 'no-store');
  if (req.method === 'OPTIONS') {
    res.writeHead(204);
    res.end();
    return;
  }
  const url = new URL(req.url, 'http://localhost');
  let body = '';
  for await (const chunk of req) body += chunk;
  if (url.pathname === '/fixture/mode' && req.method === 'POST') {
    mode = JSON.parse(body).mode;
    send(res, 200, { mode });
    return;
  }
  if (url.pathname === '/auth/me') {
    send(
      res,
      mode === 'guest' ? 401 : 200,
      mode === 'guest'
        ? { detail: 'Sign in required' }
        : {
            id: 1,
            nickname: 'BrowserTest',
            email: 'fixture@example.test',
            role: mode === 'user' ? 'user' : 'admin',
          },
    );
    return;
  }
  if (url.pathname === '/auth/csrf') {
    send(res, 200, { csrf_token: 'test-only' });
    return;
  }
  if (mode === 'error') {
    send(res, 503, { detail: 'Test connection interrupted. Refresh to retry.' });
    return;
  }
  if (mode === 'slow') await new Promise((resolve) => setTimeout(resolve, 1500));
  if (url.pathname === '/quests/139000025/explanation') {
    send(res, 200, {
      status: mode === 'empty' ? 'pending' : 'available',
      explanation: mode === 'empty' ? null : adaptiveExplanation,
    });
    return;
  }
  if (url.pathname === '/admin/story-agent/revisits') {
    send(res, 200, {
      revisits:
        mode === 'empty' || url.searchParams.has('before')
          ? []
          : [
              {
                id: 2,
                document_id: 1,
                release_id: 2,
                run_id: 14,
                status: 'paused_steps',
                candidate_count: 3,
                error: null,
              },
              {
                id: 1,
                document_id: 1,
                release_id: 2,
                run_id: null,
                status: 'no_candidates',
                candidate_count: 0,
                error: null,
              },
            ],
      next_before: url.searchParams.has('before') ? null : 1,
    });
    return;
  }
  if (url.pathname === '/admin/story-agent/alerts') {
    send(res, 200, {
      alerts: resolved
        ? []
        : [
            {
              id: 1,
              run_id: 14,
              kind: 'missing_data',
              text: 'A source passage needs editorial review.',
              node_id: 27696,
              citations: [],
            },
          ],
      next_before: null,
    });
    return;
  }
  if (url.pathname === '/admin/story-agent/alerts/1/resolve' && req.method === 'POST') {
    if (req.headers['x-csrf-token'] !== 'test-only') {
      send(res, 403, { detail: 'CSRF verification failed' });
      return;
    }
    resolved = true;
    send(res, 200, { id: 1, status: 'resolved' });
    return;
  }
  if (url.pathname === '/admin/story-agent/usage') {
    send(res, 200, {
      daily_budget_usd: '1',
      daily_token_limit: 0,
      timezone: 'Europe/Moscow',
      today: '2026-10-04',
      days: [
        {
          day: '2026-10-04',
          spent_usd: '0.09216682',
          reserved_usd: '0',
          spent_tokens: 699854,
          reserved_tokens: 0,
        },
      ],
    });
    return;
  }
  if (url.pathname === '/admin/story-agent/jobs' && req.method === 'GET') {
    send(res, 200, {
      jobs: mode === 'empty' || url.searchParams.has('before') ? [] : jobs,
      next_before: url.searchParams.has('before') ? null : 11,
    });
    return;
  }
  const match = url.pathname.match(/^\/admin\/story-agent\/jobs\/(\d+)(\/resume)?$/);
  if (match) {
    const job = jobs.find((job) => job.id === Number(match[1]));
    if (!job) {
      send(res, 404, { detail: 'Story analysis job not found' });
      return;
    }
    if (match[2]) {
      const input = JSON.parse(body);
      if (req.headers['x-csrf-token'] !== 'test-only') {
        send(res, 403, { detail: 'CSRF verification failed' });
        return;
      }
      if (
        job.status !== 'paused_steps' ||
        !Number.isInteger(input.extra_steps) ||
        input.extra_steps < 1
      ) {
        send(res, 422, { detail: 'Increase extra_steps to resume' });
        return;
      }
      job.max_steps += input.extra_steps;
      job.status = 'queued';
      job.error = null;
      console.log(`resume run=${job.id} extra_steps=${input.extra_steps} csrf=verified`);
      send(res, 200, { id: job.id, status: job.status });
      return;
    }
    send(res, 200, {
      ...job,
      limits: {
        ...job,
        provider: 'responses',
        max_input_tokens: 250000,
        max_tool_calls_per_step: 20,
      },
      document_id: job.status === 'completed' ? 1 : null,
      calls: [
        {
          id: 1,
          step: 0,
          kind: 'analysis',
          status: 'completed',
          model: job.model,
          reserved_usd: '0.034',
          cost_usd: '0.005',
          input_tokens: 12000,
          output_tokens: 128,
          retry_at: null,
          provider_error: null,
        },
      ],
    });
    return;
  }
  if (url.pathname === '/admin/story-agent/jobs' && req.method === 'POST') {
    const input = JSON.parse(body);
    if (input.quest_id === 999) {
      send(res, 422, { detail: 'Quest not found in this game version' });
      return;
    }
    send(res, 202, { id: 14, status: jobs[0].status });
    return;
  }
  send(res, 404, { detail: 'Fixture route not found' });
}).listen(8012, '127.0.0.1', () => console.log('Browser fixture API: http://localhost:8012'));
