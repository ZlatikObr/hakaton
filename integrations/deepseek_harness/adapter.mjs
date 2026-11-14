import { defineTool } from '@deepseek-ai/dsh-tools';

export const name = 'tz-audit';
export const inject = ['tools'];

/** Mount inside a dedicated agent scope. Config is host-owned, never model input.
 * The outer DSH model only orchestrates a job; the bounded Python agent does analysis.
 * Never put raw documents, credentials or report bodies in DSH prompts/session logs.
 */
export function apply(ctx, config, fetchImpl = fetch) {
  if (!ctx.tools?.guard || !ctx.tools?.restrict) throw new Error('Harness policy API unavailable');
  if (!/^[a-f0-9]{32}$/.test(config.jobId ?? '')) throw new Error('Invalid job ID');
  if (!/^[a-f0-9]{64}$/.test(config.approvedDigest ?? '')) throw new Error('A locally reviewed digest is required');
  if (typeof config.token !== 'string' || config.token.length < 24) throw new Error('Local API token required');
  const base = new URL(config.baseUrl ?? 'http://127.0.0.1:8000');
  if (base.protocol !== 'http:' || base.hostname !== '127.0.0.1' || base.username || base.password || base.pathname !== '/' || base.search || base.hash) {
    throw new Error('Only the fixed loopback backend is allowed');
  }
  let started = false;
  const tool = defineTool({
    name: 'run_procurement_review',
    description: 'Run the locally approved procurement review. The report stays on the local backend.',
    parameters: {},
    output: {
      schema: { type: 'object', properties: { status: { type: 'string', required: true }, findings: { type: 'integer', required: true } }, additionalProperties: false },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
    },
    async execute(_args, exec) {
      if (started) return { status: 'already_started', findings: 0 };
      started = true;
      try {
        const response = await fetchImpl(new URL(`/jobs/${config.jobId}/analyze`, base), {
          method: 'POST', redirect: 'error', signal: exec.signal,
          headers: { Authorization: `Bearer ${config.token}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({ mode: 'openrouter', approved_digest: config.approvedDigest }),
        });
        if (!response.ok) return { status: 'backend_rejected', findings: 0 };
        const report = await response.json();
        if (!Array.isArray(report?.review?.findings) || report.review.findings.length > 80) {
          return { status: 'invalid_response', findings: 0 };
        }
        // Return neither canonical report nor rendered excerpts: both may contain PII.
        return { status: 'complete', findings: report.review.findings.length };
      } catch {
        return { status: 'request_failed', findings: 0 };
      }
    },
  });
  ctx.tools.register(tool);
  ctx.tools.restrict({ allow: ['run_procurement_review'] });
  ctx.tools.guard(execution => execution.name === 'run_procurement_review' ? undefined : 'Tool denied by procurement policy');
}
