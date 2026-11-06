import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi } from 'vitest';
import type { ProviderOut } from '../../api/types';
import { BalancesStrip } from './BalancesStrip';

vi.mock('../../api/subscription', () => ({ useClaudeSubscription: () => ({ data: undefined }) }));
vi.mock('../../api/system', () => ({ useCanChange: () => true }));

function provider(over: Partial<ProviderOut> & Pick<ProviderOut, 'id' | 'type' | 'name'>): ProviderOut {
  return {
    enabled: true,
    base_url: null,
    options: {},
    api_key_set: true,
    api_key_hint: '…1234',
    api_key_locked: false,
    mgmt_key_set: false,
    mgmt_key_hint: null,
    mgmt_key_locked: false,
    extra_key_set: false,
    extra_key_locked: false,
    last_refresh_at: null,
    last_refresh_status: null,
    last_refresh_message: null,
    model_count: 0,
    balance: { status: 'unsupported', currency: 'USD' },
    billing_url: null,
    balance_support: 'none',
    balance_note: '',
    ...over,
  };
}

function renderStrip(providers: ProviderOut[]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MantineProvider>
        <MemoryRouter>
          <BalancesStrip providers={providers} />
        </MemoryRouter>
      </MantineProvider>
    </QueryClientProvider>,
  );
}

describe('BalancesStrip: OpenCode', () => {
  it('merges the Console and Go rows into one card', () => {
    renderStrip([
      provider({ id: 1, type: 'opencode', name: 'OpenCode Console' }),
      provider({ id: 2, type: 'opencode-go', name: 'OpenCode Go' }),
    ]);
    expect(screen.getAllByRole('button', { name: /^Refresh / })).toHaveLength(1);
    expect(screen.getByRole('button', { name: 'Refresh OpenCode Go and OpenCode Console' })).toBeInTheDocument();
  });

  it('gives a second OpenCode provider a card of its own, with its refresh error', () => {
    renderStrip([
      provider({ id: 1, type: 'opencode', name: 'OpenCode Console' }),
      provider({
        id: 2,
        type: 'opencode',
        name: 'OpenCode work',
        last_refresh_status: 'error',
        last_refresh_message: 'The key was refused.',
      }),
    ]);
    expect(screen.getByText('OpenCode work')).toBeInTheDocument();
    expect(screen.getByText('Refresh failed')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Refresh OpenCode work' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Refresh OpenCode Console' })).toBeInTheDocument();
  });

  it('keeps a Go row beyond the first out of the merged card too', () => {
    renderStrip([
      provider({ id: 1, type: 'opencode-go', name: 'OpenCode Go' }),
      provider({ id: 2, type: 'opencode', name: 'OpenCode Console' }),
      provider({ id: 3, type: 'opencode-go', name: 'OpenCode Go 2' }),
    ]);
    expect(screen.getByRole('button', { name: 'Refresh OpenCode Go and OpenCode Console' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Refresh OpenCode Go 2' })).toBeInTheDocument();
  });
});
