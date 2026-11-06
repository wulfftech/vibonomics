import { MantineProvider } from '@mantine/core';
import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { BalanceOut, ProviderOut } from '../../api/types';
import { BalanceBody } from './BalanceBody';

function provider(balance: Partial<BalanceOut>): ProviderOut {
  return {
    id: 1,
    name: 'Moonshot (China)',
    type: 'moonshot',
    balance: { status: 'ok', currency: 'USD', ...balance },
  } as unknown as ProviderOut;
}

function drawn(balance: Partial<BalanceOut>): string {
  const { container } = render(
    <MantineProvider>
      <BalanceBody provider={provider(balance)} />
    </MantineProvider>,
  );
  return container.textContent ?? '';
}

describe('BalanceBody', () => {
  it('writes a CNY balance in yuan, not as dollars', () => {
    const text = drawn({ currency: 'CNY', account_balance: 350 });
    expect(text).toContain('¥350 left');
    expect(text).not.toContain('$');
  });

  it('puts the currency on every figure it prints: spend, the 30-day total and the key limit', () => {
    const text = drawn({
      currency: 'CNY',
      account_used: 80,
      key_usage: 12.5,
      key_limit: 100,
      details: { last_30_days: 40 },
    });
    expect(text).toContain('Spent ¥80');
    expect(text).toContain('Last 30 days ¥40');
    expect(text).toContain('Key: ¥12.50 of ¥100 limit');
    expect(text).not.toContain('$');
  });

  it('names a currency it has no symbol for', () => {
    expect(drawn({ currency: 'CHF', account_balance: 12.5 })).toContain('CHF\u00a012.50 left');
  });

  it('keeps dollars for USD', () => {
    expect(drawn({ currency: 'USD', account_balance: 12.5 })).toContain('$12.50 left');
  });
});
