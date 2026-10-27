import type { JudgeOut } from '../api/types';

/** Select values for judge models: "<provider_id>|<model_id>". */
export function judgeValue(judge: { provider_id: number; model_id: string }): string {
  return `${judge.provider_id}|${judge.model_id}`;
}

/** "Model name — provider (score)" */
export function judgeLabel(judge: JudgeOut): string {
  const score = judge.composite !== null ? ` (${Math.round(judge.composite)})` : '';
  return `${judge.name} — ${judge.provider_name}${score}${judge.note ? ' — may be rate-limited' : ''}`;
}

export function parseJudge(value: string): { provider_id: number; model_id: string } | null {
  const bar = value.indexOf('|');
  if (bar < 0) {
    return null;
  }
  const providerId = Number(value.slice(0, bar));
  return Number.isFinite(providerId) ? { provider_id: providerId, model_id: value.slice(bar + 1) } : null;
}
