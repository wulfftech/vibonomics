import type { ExtraUsage, ProviderOut, SpendLimit, SubscriptionSnapshot, SubscriptionWindow } from '../api/types';
import { parseDate, relativeTime } from './format';

export interface ClaudeUsage {
  windows: SubscriptionWindow[];
  extra: ExtraUsage | null;
  spendLimit: SpendLimit | null;
  plan: string | null;
  live: boolean;
  updatedAt: string | null;
  error: string | null;
  model: string | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** The Anthropic provider's balance details carry login-token usage, if one is configured. */
function oauthUsage(providers: ProviderOut[]): {
  windows: SubscriptionWindow[];
  extra: ExtraUsage | null;
  spendLimit: SpendLimit | null;
  plan: string | null;
  error: string | null;
  updatedAt: string | null;
} | null {
  const anthropic = providers.find((provider) => provider.type === 'anthropic');
  const details = anthropic?.balance?.details;
  if (!isRecord(details)) {
    return null;
  }
  const subscription = isRecord(details.subscription) ? details.subscription : null;
  if (subscription === null) {
    const error = typeof details.subscription_error === 'string' ? details.subscription_error : null;
    return error
      ? {
          windows: [],
          extra: null,
          spendLimit: null,
          plan: null,
          error,
          updatedAt: anthropic?.balance?.fetched_at ?? null,
        }
      : null;
  }
  return {
    windows: Array.isArray(subscription.windows) ? (subscription.windows as SubscriptionWindow[]) : [],
    extra: isRecord(subscription.extra_usage) ? (subscription.extra_usage as ExtraUsage) : null,
    spendLimit: isRecord(subscription.spend_limit) ? (subscription.spend_limit as SpendLimit) : null,
    plan: typeof subscription.plan === 'string' ? subscription.plan : null,
    error: typeof details.subscription_error === 'string' ? details.subscription_error : null,
    updatedAt: anthropic?.balance?.fetched_at ?? null,
  };
}

/** Merge the live status-line snapshot with login-token data from the Anthropic provider. */
export function claudeUsage(snapshot: SubscriptionSnapshot | null, providers: ProviderOut[]): ClaudeUsage | null {
  const oauth = oauthUsage(providers);
  const liveWindows = snapshot?.windows ?? [];
  const windows = liveWindows.length > 0 ? liveWindows : (oauth?.windows ?? []);
  const extra = snapshot?.extra_usage ?? oauth?.extra ?? null;
  const spendLimit = snapshot?.spend_limit ?? oauth?.spendLimit ?? null;
  const plan = snapshot?.plan ?? oauth?.plan ?? null;
  const error = oauth?.error ?? null;
  const updatedAt = snapshot?.received_at ?? oauth?.updatedAt ?? null;
  if (windows.length === 0 && extra === null && spendLimit === null && error === null && snapshot === null) {
    return null;
  }
  return {
    windows,
    extra,
    spendLimit,
    plan,
    live: snapshot !== null && liveWindows.length > 0,
    updatedAt,
    error,
    model: snapshot?.model ?? null,
  };
}

export function usageColor(percent: number): string {
  if (percent >= 90) {
    return 'red';
  }
  if (percent >= 70) {
    return 'orange';
  }
  if (percent >= 40) {
    return 'yellow';
  }
  return 'teal';
}

/**
 * Relative reset ("in 4 h") for normal windows. A reset more than 30 days away is almost always a
 * bogus timestamp (or a badly skewed clock), so show the date instead of "in 3 years".
 */
export function resetLabel(value: string | null | undefined): string | null {
  if (!value) {
    return null;
  }
  const date = parseDate(value);
  if (date === null) {
    return null;
  }
  const days = (date.getTime() - Date.now()) / 86_400_000;
  if (days > 30) {
    return `on ${date.toISOString().slice(0, 10)}`;
  }
  return relativeTime(value);
}
