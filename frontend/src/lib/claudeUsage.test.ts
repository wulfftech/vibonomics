import { describe, expect, it } from 'vitest';
import { claudeUsage, resetLabel, usageColor } from './claudeUsage';
import type { ProviderOut, SubscriptionSnapshot } from '../api/types';

const snapshot: SubscriptionSnapshot = {
  source: 'claude-code',
  received_at: new Date().toISOString(),
  windows: [
    {
      key: 'five_hour',
      label: '5-hour session',
      used_percentage: 23.5,
      resets_at: new Date(Date.now() + 2 * 3600_000).toISOString(),
    },
    { key: 'seven_day', label: 'Weekly', used_percentage: 80, resets_at: '2030-01-01T00:00:00Z' },
  ],
  model: 'Opus',
};

describe('resetLabel', () => {
  it('uses relative time for near resets', () => {
    const soon = new Date(Date.now() + 2 * 3600_000).toISOString();
    expect(resetLabel(soon)).toBe('in 2 h');
  });

  it('shows the date for implausible far-future resets', () => {
    expect(resetLabel('2030-01-01T00:00:00Z')).toBe('on 2030-01-01');
  });

  it('returns null without a value', () => {
    expect(resetLabel(null)).toBeNull();
    expect(resetLabel(undefined)).toBeNull();
    expect(resetLabel('not a date')).toBeNull();
  });
});

describe('usageColor', () => {
  it('escalates with usage', () => {
    expect(usageColor(10)).toBe('teal');
    expect(usageColor(50)).toBe('yellow');
    expect(usageColor(75)).toBe('orange');
    expect(usageColor(95)).toBe('red');
  });
});

describe('claudeUsage', () => {
  it('prefers live windows and keeps login-token credits', () => {
    const providers = [
      {
        id: 1,
        type: 'anthropic',
        balance: {
          details: {
            subscription: {
              windows: [{ key: 'five_hour', label: '5-hour', used_percentage: 1, resets_at: null }],
              extra_usage: { used: 12.5, limit: 20, utilization: 0.625, currency: 'USD', monthly: true },
              plan: 'max',
            },
          },
          fetched_at: new Date().toISOString(),
        },
      } as unknown as ProviderOut,
    ];
    const usage = claudeUsage(snapshot, providers);
    expect(usage?.live).toBe(true);
    expect(usage?.windows[0].label).toBe('5-hour session');
    expect(usage?.extra?.used).toBe(12.5);
    expect(usage?.plan).toBe('max');
  });

  it('returns null when there is nothing to show', () => {
    expect(claudeUsage(null, [])).toBeNull();
  });
});
