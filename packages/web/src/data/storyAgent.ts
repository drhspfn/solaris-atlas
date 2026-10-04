import { APP_SETTINGS } from '../config/settings.ts';

export type AgentRequest = { quest_id: number; game_version: string; locale: string };
export type AgentJob = {
  mode?: 'analysis' | 'revisit';
  id: number;
  status: string;
  request: AgentRequest | null;
  step: number;
  max_steps: number;
  model: string;
  error: string | null;
  cost_usd: number | null;
  tokens_input: number | null;
  tokens_output: number | null;
};
export type AgentCall = {
  id: number;
  step: number;
  kind: string;
  status: string;
  model: string;
  reserved_usd: string;
  cost_usd: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  retry_at: string | null;
  provider_error: { code: string; retry_after_seconds: number | null } | null;
};
export type AgentDetail = Omit<AgentJob, 'max_steps' | 'model'> & {
  assessment?: { narrative_weight: string; hook_priority: string; reason: string } | null;
  policy?: { depth: string; words: number } | null;
  limits: {
    max_steps: number;
    max_input_tokens: number;
    max_output_tokens: number;
    max_tool_calls_per_step: number;
    provider: string;
    model: string;
  };
  document_id: number | null;
  calls: AgentCall[];
};
export type AgentUsage = {
  daily_budget_usd: string;
  daily_token_limit: number;
  timezone: string;
  today: string;
  days: {
    day: string;
    spent_usd: string;
    reserved_usd: string;
    spent_tokens: number;
    reserved_tokens: number;
  }[];
};

export function resumePolicy(job: AgentDetail): { allowed: boolean; help: string } {
  if (job.status === 'paused_output') {
    const allowed =
      job.limits.max_output_tokens < APP_SETTINGS.storyAgent.maxOutputTokens && job.step < 99;
    return {
      allowed,
      help: allowed
        ? `The response reached its ${job.limits.max_output_tokens.toLocaleString('en-US')}-token limit. Continue with a larger allowance from saved research. The incomplete response remains charged; the next response uses the daily budget.`
        : 'The response reached the maximum allowance or step limit. Split the analysis before starting another run.',
    };
  }
  if (job.status === 'paused_steps')
    return {
      allowed: job.limits.max_steps < 100,
      help:
        job.limits.max_steps < 100
          ? 'Add research steps and continue from the saved checkpoint. Completed requests are not repeated.'
          : 'This run has reached the maximum of 100 steps. Review its alerts and split the research before starting another run.',
    };
  if (job.status === 'paused_context')
    return {
      allowed: job.limits.provider === 'responses',
      help: 'Compact the saved conversation and continue. Read sources and citation checks are preserved.',
    };
  if (job.status === 'paused_uncertain')
    return {
      allowed: false,
      help: 'A request has an unknown billing outcome. Verify provider usage and reconcile it before spending again. Automatic resume is blocked.',
    };
  if (job.status === 'paused_budget')
    return {
      allowed: true,
      help: 'The daily allowance cannot cover the next request reservation. Continue after it resets or the server allowance is updated. This action does not raise the budget.',
    };
  if (job.status === 'paused_rate_limit')
    return {
      allowed: true,
      help: 'The provider requested a cooldown. Resume respects the saved retry time and reuses the existing reservation.',
    };
  if (job.status === 'paused_provider')
    return {
      allowed: true,
      help: 'Check the provider quota and billing settings before retrying. The existing reservation is retained.',
    };
  if (['paused_config', 'enqueue_failed'].includes(job.status))
    return {
      allowed: true,
      help: 'Restore the matching worker configuration or queue connection, then continue this run.',
    };
  return {
    allowed: false,
    help:
      job.status === 'completed'
        ? 'Published. Open the quest to read the explanation and inspect its sources.'
        : ['queued', 'running'].includes(job.status)
          ? 'The worker will continue automatically. This view refreshes while open.'
          : 'Review the recorded failure and source alerts before starting another analysis.',
  };
}

export function resumeBody(job: AgentDetail, extraSteps: number): Record<string, number | boolean> {
  if (!resumePolicy(job).allowed) throw new Error('This run cannot be resumed from the panel.');
  if (job.status === 'paused_output')
    return {
      output_tokens: Math.min(
        APP_SETTINGS.storyAgent.maxOutputTokens,
        Math.max(APP_SETTINGS.storyAgent.outputRecoveryMinTokens, job.limits.max_output_tokens * 2),
      ),
      extra_steps: Math.max(0, job.step + 2 - job.limits.max_steps),
    };
  if (job.status === 'paused_steps') {
    if (!Number.isInteger(extraSteps) || extraSteps < 1 || extraSteps > 100 - job.limits.max_steps)
      throw new Error('Choose a positive number of steps within the remaining limit.');
    return { extra_steps: extraSteps };
  }
  return job.status === 'paused_context' ? { compact_context: true } : {};
}

export function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    queued: 'Queued',
    running: 'Running',
    completed: 'Published',
    paused_steps: 'Step limit',
    paused_context: 'Context limit',
    paused_output: 'Response limit',
    paused_budget: 'Budget limit',
    paused_rate_limit: 'Cooldown',
    paused_provider: 'Provider rejected',
    paused_config: 'Configuration',
    paused_uncertain: 'Billing review',
    enqueue_failed: 'Queue unavailable',
    failed: 'Failed',
    stale: 'Sources changed',
  };
  return labels[status] ?? status.replaceAll('_', ' ');
}
