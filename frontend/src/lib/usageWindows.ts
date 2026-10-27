import type { BalanceOut } from '../api/types';

/** A usage window as the UI draws it: a percentage, a reset time and an optional limit note. */
export interface UsageWindowView {
  key: string;
  label: string;
  used_percentage: number;
  resets_at?: string | null;
  limit_reached?: boolean;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/**
 * Windows a provider publishes in its balance details. OpenCode Go is the one today: its usage
 * endpoint reports rolling, weekly and monthly percentages of the plan's included allowance. The
 * shape is checked field by field because the response comes from an undocumented endpoint.
 */
export function balanceWindows(balance: BalanceOut | null | undefined): UsageWindowView[] {
  const details = balance?.details;
  if (!isRecord(details) || !Array.isArray(details.usage_windows)) {
    return [];
  }
  const windows: UsageWindowView[] = [];
  for (const entry of details.usage_windows) {
    if (!isRecord(entry) || typeof entry.used_percentage !== 'number') {
      continue;
    }
    if (!Number.isFinite(entry.used_percentage)) {
      continue;
    }
    const status = typeof entry.status === 'string' ? entry.status : '';
    windows.push({
      key: typeof entry.key === 'string' ? entry.key : `window-${windows.length}`,
      label: typeof entry.label === 'string' ? entry.label : 'Usage',
      used_percentage: Math.max(0, Math.min(100, entry.used_percentage)),
      resets_at: typeof entry.resets_at === 'string' ? entry.resets_at : null,
      limit_reached: status.includes('limit'),
    });
  }
  return windows;
}
