import { MantineProvider } from '@mantine/core';
import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { ClaudeUsage } from '../../lib/claudeUsage';
import { SubscriptionDetails } from './SubscriptionDetails';

function drawn(currency: string): string {
  const usage = {
    windows: [],
    extra: { used: 12.5, limit: 20, utilization: 0.625, currency, monthly: true },
    spendLimit: null,
    session: null,
    plan: null,
    live: true,
    updatedAt: null,
    error: null,
    model: null,
  } satisfies ClaudeUsage;
  const { container } = render(
    <MantineProvider>
      <SubscriptionDetails usage={usage} />
    </MantineProvider>,
  );
  return container.textContent ?? '';
}

describe('SubscriptionDetails', () => {
  it('writes the usage credits in the currency the account reports', () => {
    expect(drawn('USD')).toContain('$12.50 of $20.00');
    expect(drawn('AUD')).toContain('AUD\u00a012.50 of AUD\u00a020.00');
  });
});
