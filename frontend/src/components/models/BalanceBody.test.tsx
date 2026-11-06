import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ProviderOut } from '../../api/types';
import { BalanceFooter } from './BalanceBody';

let canChange = true;
vi.mock('../../api/system', () => ({ useCanChange: () => canChange }));

const fetchMock = vi.fn();

function provider(fetchedAt: string | null): ProviderOut {
  return {
    id: 7,
    type: 'moonshot',
    name: 'Kimi',
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
    model_count: 3,
    balance: fetchedAt === null ? null : { status: 'ok', currency: 'USD', account_balance: 5, fetched_at: fetchedAt },
    billing_url: null,
    balance_support: 'key',
    balance_note: '',
  };
}

function footer(fetchedAt: string | null) {
  return (
    <QueryClientProvider client={client}>
      <MantineProvider>
        <MemoryRouter>
          <BalanceFooter providers={[provider(fetchedAt)]} />
        </MemoryRouter>
      </MantineProvider>
    </QueryClientProvider>
  );
}

let client: QueryClient;

const button = () => screen.getByRole('button', { name: 'Refresh Kimi' });
const refreshCalls = () => fetchMock.mock.calls.filter(([url]) => url === 'api/providers/refresh');

describe('BalanceFooter', () => {
  beforeEach(() => {
    canChange = true;
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    fetchMock.mockReset();
    fetchMock.mockImplementation((url: string) =>
      Promise.resolve(
        url === 'api/providers/refresh'
          ? new Response(JSON.stringify({ status: 'started' }), { status: 202 })
          : new Response(JSON.stringify({ detail: `unexpected ${url}` }), { status: 500 }),
      ),
    );
    vi.stubGlobal('fetch', fetchMock);
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('refreshes the card for someone who can change things', async () => {
    render(footer('2026-11-01T00:00:00Z'));
    expect(button()).toBeEnabled();
    fireEvent.click(button());
    await waitFor(() => expect(refreshCalls()).toHaveLength(1));
    expect(JSON.parse((refreshCalls()[0][1] as RequestInit).body as string)).toEqual({ provider_ids: [7] });
  });

  it('disables the refresh button for a visitor to an instance that is open to read', () => {
    canChange = false;
    render(footer('2026-11-01T00:00:00Z'));
    expect(button()).toBeDisabled();
    fireEvent.click(button());
    expect(refreshCalls()).toHaveLength(0);
  });

  it('keeps spinning after the request is accepted, until a newer balance arrives', async () => {
    const view = render(footer('2026-11-01T00:00:00Z'));
    fireEvent.click(button());
    await waitFor(() => expect(refreshCalls()).toHaveLength(1));
    await waitFor(() => expect(client.isMutating()).toBe(0));
    expect(button()).toHaveAttribute('data-loading', 'true');

    view.rerender(footer('2026-11-01T00:00:00Z'));
    expect(button()).toHaveAttribute('data-loading', 'true');

    view.rerender(footer('2026-11-01T00:05:00Z'));
    expect(button()).not.toHaveAttribute('data-loading');
  });

  it('stops spinning when the request is refused', async () => {
    fetchMock.mockImplementation(() =>
      Promise.resolve(new Response(JSON.stringify({ detail: 'Provider not found' }), { status: 404 })),
    );
    render(footer('2026-11-01T00:00:00Z'));
    fireEvent.click(button());
    await waitFor(() => expect(refreshCalls()).toHaveLength(1));
    await waitFor(() => expect(button()).not.toHaveAttribute('data-loading'));
  });

  it('gives up waiting for a balance that never arrives', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(footer('2026-11-01T00:00:00Z'));
    fireEvent.click(button());
    await waitFor(() => expect(refreshCalls()).toHaveLength(1));
    await waitFor(() => expect(client.isMutating()).toBe(0));
    expect(button()).toHaveAttribute('data-loading', 'true');

    await act(async () => {
      await vi.advanceTimersByTimeAsync(130_000);
    });
    expect(button()).not.toHaveAttribute('data-loading');
  });
});
