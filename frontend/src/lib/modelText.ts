import type { ModelRow, UsageProfile } from '../api/types';
import { compactTokens, money } from './format';
import type { EnrichedModel } from './modelTable';

/** Score colour bands: ≥80 green, ≥60 teal, ≥40 yellow, else gray. */
export function scoreColor(value: number): string {
  return value >= 80 ? 'green' : value >= 60 ? 'teal' : value >= 40 ? 'yellow' : 'gray';
}

/**
 * The labels `ModelBadges` and `ModelNameCell` draw after a model's name, in order. The table measures
 * them to size the Model column. The discount has its own column and no badge.
 */
export function modelBadgeLabels(row: ModelRow, alsoOnGo = false): string[] {
  const labels: string[] = [];
  if (row.is_new) {
    labels.push('New');
  }
  if (row.new_discount) {
    labels.push('New discount');
  }
  if (row.price_drop) {
    labels.push('Price cut');
  }
  if (row.is_free) {
    labels.push('Free');
  }
  if (!row.active) {
    labels.push('Inactive');
  }
  if (alsoOnGo) {
    labels.push('also on Go');
  }
  return labels;
}

export function discountTooltip(row: ModelRow): string {
  const parts: string[] = [];
  if (row.discount_note) {
    parts.push(row.discount_note);
  }
  if (row.list_price) {
    parts.push(
      `List ${money(row.list_price.input)} / ${money(row.list_price.output)} → now ${money(row.price.input)} / ${money(row.price.output)} per 1M`,
    );
  }
  if (row.discount_until) {
    parts.push(`Until ${row.discount_until}`);
  }
  return parts.join(' · ') || 'Discounted';
}

/** "anthropic · 1M ctx · tools · reasoning" */
export function modelSubline(row: ModelRow): string {
  const parts: string[] = [];
  if (row.creator) {
    parts.push(row.creator);
  }
  if (row.context_length) {
    parts.push(`${compactTokens(row.context_length)} ctx`);
  }
  if (row.tool_call) {
    parts.push('tools');
  }
  if (row.reasoning) {
    parts.push('reasoning');
  }
  return parts.join(' · ');
}

export function taskCostExplanation(model: EnrichedModel, profile: UsageProfile): string {
  const tokens = `${compactTokens(profile.task_input_tokens)} in + ${compactTokens(profile.task_output_tokens)} out`;
  const estimate = `Estimated for "${profile.name}" (${tokens}): ${money(model.estimatedTaskCost)}.`;
  switch (model.taskCostSource) {
    case 'measured':
      return `Measured by Vibonomics tests: average ${money(model.taskCost)} per task over ${model.row.test?.tasks ?? 0} tasks. ${estimate}`;
    case 'reported':
      return `Reported by benchmark sources (median cost per task): ${money(model.taskCost)}. ${estimate}`;
    case 'estimated':
      return estimate;
    default:
      return 'Prices unknown';
  }
}
