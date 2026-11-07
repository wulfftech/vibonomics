import { describe, expect, it } from 'vitest';
import type { BalanceOut } from '../api/types';
import { balanceWindows, freeModelWindow } from './usageWindows';

// 2026-11-07 10:00 UTC; the next reset is the following midnight.
const NOW = Date.parse('2026-11-07T10:00:00Z');
const RESET = '2026-11-08T00:00:00.000Z';

function balance(quota: unknown, fetched_at = '2026-11-07T09:45:00Z', extra: Record<string, unknown> = {}): BalanceOut {
  return {
    status: 'ok',
    currency: 'USD',
    fetched_at,
    details: { key: { free_model_daily_requests: quota }, ...extra },
  };
}

describe('freeModelWindow', () => {
  it('shows the requests used of the daily cap, and when the cap resets', () => {
    const window = freeModelWindow(balance({ used: 120, limit: 1000, remaining: 880 }), NOW);
    expect(window).toMatchObject({
      key: 'free-models',
      label: 'Free models today',
      used_percentage: 12,
      resets_at: RESET,
      limit_reached: false,
    });
    expect(window?.detail).toBe('120 of 1,000 requests, shared by every free model');
  });

  it('says the limit is reached when none are left', () => {
    expect(freeModelWindow(balance({ used: 1000, limit: 1000, remaining: 0 }), NOW)).toMatchObject({
      used_percentage: 100,
      limit_reached: true,
    });
    expect(freeModelWindow(balance({ used: 40, limit: 50, remaining: 0 }), NOW)?.limit_reached).toBe(true);
  });

  it('never goes past a full bar', () => {
    expect(freeModelWindow(balance({ used: 1400, limit: 1000, remaining: 0 }), NOW)?.used_percentage).toBe(100);
  });

  it('treats a figure from before the last reset as reset, not as a count', () => {
    const window = freeModelWindow(balance({ used: 900, limit: 1000, remaining: 100 }, '2026-11-06T22:00:00Z'), NOW);
    expect(window).toMatchObject({ used_percentage: 0, limit_reached: false, resets_at: RESET });
    expect(window?.detail).toContain('reset at 00:00 UTC');
    expect(window?.detail).not.toContain('900');
  });

  it.each([
    ['no quota', undefined],
    ['a quota that is not an object', 'lots'],
    ['no limit', { used: 1 }],
    ['a zero limit', { used: 0, limit: 0 }],
    ['a negative use', { used: -1, limit: 1000 }],
    ['a use that is not a number', { used: '5', limit: 1000 }],
    ['a limit that is not finite', { used: 5, limit: Number.POSITIVE_INFINITY }],
  ])('draws nothing for %s', (_name, quota) => {
    expect(freeModelWindow(balance(quota), NOW)).toBeNull();
  });

  it('draws nothing for a provider with no key details, or no balance at all', () => {
    expect(freeModelWindow({ status: 'ok', currency: 'USD', details: {} }, NOW)).toBeNull();
    expect(freeModelWindow(null, NOW)).toBeNull();
    expect(freeModelWindow(undefined, NOW)).toBeNull();
  });
});

describe('balanceWindows', () => {
  it('puts the free-model cap in front of any plan windows a provider publishes', () => {
    const windows = balanceWindows(
      balance({ used: 5, limit: 50, remaining: 45 }, new Date().toISOString(), {
        usage_windows: [{ key: 'weekly', label: 'Weekly', used_percentage: 30 }],
      }),
    );
    expect(windows.map((w) => w.key)).toEqual(['free-models', 'weekly']);
  });

  it('still returns nothing for a balance with neither', () => {
    expect(balanceWindows({ status: 'ok', currency: 'USD', details: {} })).toEqual([]);
  });
});
