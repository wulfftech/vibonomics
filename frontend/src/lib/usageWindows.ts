import type { BalanceOut } from '../api/types';

/** A usage window as the UI draws it: a percentage, a reset time and an optional limit note. */
export interface UsageWindowView {
  key: string;
  label: string;
  used_percentage: number;
  resets_at?: string | null;
  limit_reached?: boolean;
  /** What the percentage is of, in words ("120 of 1,000 requests"). */
  detail?: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

const DAY_MS = 86_400_000;

/**
 * OpenRouter's free models share one daily request cap per account (1000 with credits, 50 without), counted from
 * 00:00 UTC. The key's own balance report carries it as `free_model_daily_requests`. The figure is as old as the last
 * balance refresh: one taken before today's reset is no longer a count of anything, so it is shown as reset rather
 * than as a use that has already been forgiven.
 */
export function freeModelWindow(balance: BalanceOut | null | undefined, now: number = Date.now()): UsageWindowView | null {
  const key = balance?.details?.key;
  const quota = isRecord(key) ? key.free_model_daily_requests : undefined;
  if (!isRecord(quota) || !finite(quota.used) || !finite(quota.limit) || quota.limit <= 0 || quota.used < 0) {
    return null;
  }
  const midnight = Math.floor(now / DAY_MS) * DAY_MS;
  const fetched = balance?.fetched_at ? new Date(balance.fetched_at).getTime() : Number.NaN;
  const resetsAt = new Date(midnight + DAY_MS).toISOString();
  const since = Number.isFinite(fetched) && fetched < midnight;
  const used = since ? 0 : quota.used;
  return {
    key: 'free-models',
    label: 'Free models today',
    used_percentage: Math.max(0, Math.min(100, (used / quota.limit) * 100)),
    resets_at: resetsAt,
    limit_reached: !since && (used >= quota.limit || (finite(quota.remaining) && quota.remaining <= 0)),
    detail: since
      ? `Counter reset at 00:00 UTC since the last update; refresh for today's count (cap ${quota.limit.toLocaleString('en-US')})`
      : `${used.toLocaleString('en-US')} of ${quota.limit.toLocaleString('en-US')} requests, shared by every free model`,
  };
}

/**
 * Windows a provider publishes in its balance details. OpenCode Go is the one today: its usage
 * endpoint reports rolling, weekly and monthly percentages of the plan's included allowance. The
 * shape is checked field by field because the response comes from an undocumented endpoint.
 */
export function balanceWindows(balance: BalanceOut | null | undefined): UsageWindowView[] {
  const details = balance?.details;
  const windows: UsageWindowView[] = [];
  const free = freeModelWindow(balance);
  if (free !== null) {
    windows.push(free);
  }
  if (!isRecord(details) || !Array.isArray(details.usage_windows)) {
    return windows;
  }
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
