import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SpendBucket, SpendOut } from '../api/types';
import { UsagePage } from './UsagePage';

let canChange = true;
vi.mock('../api/system', async () => ({
  ...(await vi.importActual<object>('../api/system')),
  useCanChange: () => canChange,
}));

function bucket(over: Partial<SpendBucket> = {}): SpendBucket {
  return {
    cost: 0,
    requests: 0,
    prompt_tokens: 0,
    completion_tokens: 0,
    reasoning_tokens: 0,
    cached_tokens: 0,
    tokens: 0,
    byok_cost: 0,
    byok_tokens: 0,
    cost_per_mtok: null,
    cost_per_request: null,
    cache_hit: null,
    ...over,
  };
}

const TOTAL = bucket({
  cost: 3.5,
  requests: 35,
  prompt_tokens: 3_200_000,
  completion_tokens: 360_000,
  tokens: 3_560_000,
  cached_tokens: 2_700_000,
  cost_per_mtok: 0.986,
  cost_per_request: 0.1,
  cache_hit: 0.84375,
});

function spend(over: Partial<SpendOut> = {}): SpendOut {
  return {
    accounts: [
      {
        id: 1,
        name: 'OpenRouter',
        has_management_key: true,
        last_synced: new Date(Date.now() - 3 * 3600_000).toISOString(),
        first_date: '2026-10-30',
        last_date: '2026-11-01',
      },
    ],
    since: '2026-10-30',
    until: '2026-11-01',
    days: 30,
    provider_id: null,
    key: null,
    totals: TOTAL,
    today_usd: 0.3,
    today_as_of: new Date().toISOString(),
    daily: [
      { date: '2026-10-30', ...bucket({ cost: 1.5, requests: 15, tokens: 1_350_000, cost_per_mtok: 1.11 }) },
      { date: '2026-10-31', ...bucket() },
      { date: '2026-11-01', ...bucket({ cost: 2, requests: 20, tokens: 2_200_000, cost_per_mtok: 0.91 }) },
    ],
    models: [
      { name: 'acme/m1', ...bucket({ cost: 3, requests: 30, tokens: 3_300_000, cost_per_mtok: 0.91, cache_hit: 0.9 }) },
      { name: 'acme/m2', ...bucket({ cost: 0.5, requests: 5, tokens: 250_000, cost_per_mtok: 2 }) },
    ],
    upstreams: [{ name: 'DeepInfra', ...bucket({ cost: 3.5, requests: 35, tokens: 3_550_000, cost_per_mtok: 0.986 }) }],
    keys: [
      {
        hash: 'hashA',
        name: 'Recall',
        disabled: false,
        last_used_at: new Date(Date.now() - 86_400_000).toISOString(),
        usage_total: 2.6,
        ...bucket({ cost: 2.6, requests: 26, tokens: 2_600_000, cost_per_mtok: 1 }),
      },
    ],
    other_keys: bucket({ cost: 0.9, requests: 9, tokens: 950_000, cost_per_mtok: 0.95 }),
    ...over,
  };
}

const fetchMock = vi.fn();

function serve(body: SpendOut) {
  fetchMock.mockImplementation((url: string) => {
    const json = (payload: unknown, status = 200) =>
      Promise.resolve(
        new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } }),
      );
    if (url.startsWith('api/usage')) {
      return json(body);
    }
    if (url === 'api/refresh') {
      return json({ running: ['usage'], last: {} }, 202);
    }
    return json({ detail: `unexpected ${url}` }, 500);
  });
  vi.stubGlobal('fetch', fetchMock);
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MantineProvider>
        <MemoryRouter>
          <UsagePage />
        </MemoryRouter>
      </MantineProvider>
    </QueryClientProvider>,
  );
}

const usageUrls = () => fetchMock.mock.calls.map(([url]) => String(url)).filter((url) => url.startsWith('api/usage'));

describe('UsagePage', () => {
  beforeEach(() => {
    fetchMock.mockReset();
    canChange = true;
    // jsdom lacks it, and Mantine's option list scrolls the active option into view.
    window.HTMLElement.prototype.scrollIntoView = () => undefined;
  });
  afterEach(() => vi.unstubAllGlobals());

  it('leads with what was spent, in what, and what a token cost', async () => {
    serve(spend());
    renderPage();
    await screen.findByRole('table', { name: 'By model' });
    const tile = (name: string) => within(screen.getByRole('group', { name }));
    expect(tile('Spend').getByText('$3.50')).toBeInTheDocument();
    expect(tile('Spend').getByText(/\$0\.30 so far today/)).toBeInTheDocument();
    expect(tile('Tokens').getByText('3.6M')).toBeInTheDocument();
    expect(tile('Tokens').getByText('3.2M in, 360k out')).toBeInTheDocument();
    expect(tile('Requests').getByText('35')).toBeInTheDocument();
    expect(tile('Cost per 1M tokens').getByText('$0.986')).toBeInTheDocument();
    expect(tile('Cached').getByText('84%')).toBeInTheDocument();
    expect(screen.getByText(/Synced 3 h ago/)).toBeInTheDocument();
  });

  it('shows what went through the operator’s own provider keys beside Spend, only when there is some', async () => {
    serve(spend({ totals: { ...TOTAL, byok_cost: 1.25, byok_tokens: 400_000 } }));
    renderPage();
    await screen.findByRole('table', { name: 'By model' });
    expect(
      within(screen.getByRole('group', { name: 'Spend' })).getByText('+ $1.25 through your own keys (400k tokens)'),
    ).toBeInTheDocument();
  });

  it('still mentions own-key tokens when they carried no reported cost', async () => {
    serve(spend({ totals: { ...TOTAL, byok_cost: 0, byok_tokens: 400_000 } }));
    renderPage();
    await screen.findByRole('table', { name: 'By model' });
    expect(
      within(screen.getByRole('group', { name: 'Spend' })).getByText('+ $0 through your own keys (400k tokens)'),
    ).toBeInTheDocument();
  });

  it('says nothing about own keys when none were used', async () => {
    serve(spend());
    renderPage();
    await screen.findByRole('table', { name: 'By model' });
    expect(screen.queryByText(/your own keys/)).toBeNull();
  });

  it('labels the per-token and per-request figures as covering credit-billed traffic only', async () => {
    serve(spend());
    renderPage();
    await screen.findByRole('table', { name: 'By model' });
    const tile = (name: string) => within(screen.getByRole('group', { name }));
    expect(tile('Cost per 1M tokens').getByText('Credit-billed traffic only')).toBeInTheDocument();
    expect(tile('Requests').getByText('$0.10 each, credit-billed traffic only')).toBeInTheDocument();
    for (const header of screen.getAllByRole('columnheader', { name: 'Per 1M tokens' })) {
      expect(header).toHaveAttribute('title', expect.stringContaining('credit-billed traffic only'));
    }
  });

  it('notes that providers sharing a management key are counted once, while All accounts is chosen', async () => {
    const second = { ...spend().accounts[0], id: 2, name: 'OpenRouter work' };
    serve(spend({ accounts: [...spend().accounts, second] }));
    const user = userEvent.setup();
    renderPage();
    expect(await screen.findByText('Providers that share a management key are counted once.')).toBeInTheDocument();

    await user.click(screen.getByRole('combobox', { name: 'Account' }));
    await user.click(await screen.findByRole('option', { name: 'OpenRouter work', hidden: true }));
    await waitFor(() => expect(usageUrls()).toContain('api/usage?days=30&provider_id=2'));
    expect(screen.queryByText('Providers that share a management key are counted once.')).toBeNull();
  });

  it('does not add that note when there is only one account to choose from', async () => {
    serve(spend());
    renderPage();
    await screen.findByRole('table', { name: 'By model' });
    expect(screen.queryByText(/share a management key/)).toBeNull();
  });

  it('breaks the spend down by model, key and provider, with the remainder no key accounts for', async () => {
    serve(spend());
    renderPage();
    const models = within(await screen.findByRole('table', { name: 'By model' }));
    expect(models.getByText('acme/m1')).toBeInTheDocument();
    expect(models.getByText('86%')).toBeInTheDocument();
    const keys = within(screen.getByRole('table', { name: 'By API key' }));
    expect(keys.getByRole('button', { name: 'Recall' })).toBeInTheDocument();
    expect(keys.getByText('Other')).toBeInTheDocument();
    expect(within(screen.getByRole('table', { name: 'By provider' })).getByText('DeepInfra')).toBeInTheDocument();
  });

  it('draws both charts and offers each as a table', async () => {
    serve(spend());
    const user = userEvent.setup();
    renderPage();
    expect(await screen.findByRole('img', { name: 'Spend per day, in dollars' })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Cost per million tokens, per day, in dollars' })).toBeInTheDocument();
    await user.click(screen.getAllByRole('radio', { name: 'Table' })[0]);
    const table = within(screen.getByRole('table', { name: 'Spend by day' }));
    expect(table.getByText('2026-10-31')).toBeInTheDocument();
    expect(table.getByText('$2.00')).toBeInTheDocument();
  });

  it('asks for a narrower slice when a range or a key is chosen', async () => {
    serve(spend());
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole('table', { name: 'By model' });
    expect(usageUrls()[0]).toBe('api/usage?days=30');

    await user.click(screen.getByRole('radio', { name: '7 days' }));
    await waitFor(() => expect(usageUrls()).toContain('api/usage?days=7'));

    await user.click(within(screen.getByRole('table', { name: 'By API key' })).getByRole('button', { name: 'Recall' }));
    await waitFor(() => expect(usageUrls()).toContain('api/usage?days=7&key=hashA'));
  });

  it('says a management key is needed when no account has one', async () => {
    serve(
      spend({
        accounts: [
          {
            id: 1,
            name: 'OpenRouter',
            has_management_key: false,
            last_synced: null,
            first_date: null,
            last_date: null,
          },
        ],
        daily: [],
      }),
    );
    renderPage();
    expect(await screen.findByText('Spend history needs a management key')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Settings' })).toHaveAttribute('href', '/settings/providers');
    expect(screen.queryByRole('table', { name: 'By model' })).toBeNull();
  });

  it('offers to fetch the history when there is none yet, to someone who can change things', async () => {
    serve(spend({ daily: [], models: [], keys: [], other_keys: null, since: null, until: null, totals: bucket() }));
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole('button', { name: 'Fetch spend history' }));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([url]) => url === 'api/refresh');
      expect(JSON.parse((call?.[1] as RequestInit).body as string)).toEqual({ kinds: ['usage'] });
    });
  });

  it('only tells a visitor to check back', async () => {
    canChange = false;
    serve(spend({ daily: [], models: [], keys: [], other_keys: null, since: null, until: null, totals: bucket() }));
    renderPage();
    expect(await screen.findByText(/Check back after the next pull/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Fetch spend history' })).toBeNull();
  });
});
