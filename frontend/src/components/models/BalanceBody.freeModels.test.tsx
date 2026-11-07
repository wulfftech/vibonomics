import { MantineProvider } from '@mantine/core';
import { render } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { BalanceOut, ProviderOut } from '../../api/types';
import { BalanceBody } from './BalanceBody';

function openrouter(balance: Partial<BalanceOut>): ProviderOut {
  return {
    id: 1,
    name: 'OpenRouter',
    type: 'openrouter',
    balance: { status: 'ok', currency: 'USD', ...balance },
  } as unknown as ProviderOut;
}

function drawn(balance: Partial<BalanceOut>): string {
  const { container } = render(
    <MemoryRouter>
      <MantineProvider>
        <BalanceBody provider={openrouter(balance)} />
      </MantineProvider>
    </MemoryRouter>,
  );
  return container.textContent ?? '';
}

describe('BalanceBody on the OpenRouter card', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-11-07T10:00:00Z'));
  });
  afterEach(() => vi.useRealTimers());

  it('shows how much of the free-model daily cap is used, beside the credit and the key', () => {
    const text = drawn({
      account_balance: 12.5,
      key_usage: 1.5,
      key_limit: 10,
      fetched_at: '2026-11-07T09:50:00Z',
      details: { key: { free_model_daily_requests: { used: 120, limit: 1000, remaining: 880 } } },
    });
    expect(text).toContain('$12.50 left');
    expect(text).toContain('Key: $1.50 of $10.00 limit');
    expect(text).toContain('Free models today');
    expect(text).toContain('120 of 1,000 requests');
    expect(text).toContain('12%');
    expect(text).toMatch(/resets in 14 h/);
  });

  it('says so when the free-model cap is spent', () => {
    const text = drawn({
      account_balance: 3,
      fetched_at: '2026-11-07T09:50:00Z',
      details: { key: { free_model_daily_requests: { used: 1000, limit: 1000, remaining: 0 } } },
    });
    expect(text).toContain('1,000 of 1,000 requests');
    expect(text).toContain('limit reached');
  });

  it('shows no free-model row when the key reported none', () => {
    const text = drawn({ account_balance: 3, details: { key: { usage_daily: 0 } } });
    expect(text).toContain('$3.00 left');
    expect(text).not.toContain('Free models today');
  });
});
