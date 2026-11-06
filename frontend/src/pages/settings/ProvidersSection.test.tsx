import { MantineProvider } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ProviderOut, ProviderTypeOut } from '../../api/types';
import { ProvidersSection } from './ProvidersSection';

vi.mock('@mantine/notifications', () => ({ notifications: { show: vi.fn() } }));

const fetchMock = vi.fn();

const TYPE: ProviderTypeOut = {
  type: 'moonshot',
  name: 'Moonshot AI (Kimi)',
  description: 'Kimi models direct from Moonshot.',
  homepage: 'https://platform.kimi.ai',
  keys_url: null,
  billing_url: null,
  default_base_url: 'https://api.moonshot.ai/v1',
  balance: 'key',
  balance_note: '',
  mgmt_key_label: null,
  mgmt_key_help: null,
  models_require_key: false,
  custom: false,
  options: [{ key: 'region', label: 'Platform', description: '', required: false, placeholder: 'global' }],
  env_key: 'VIBONOMICS_MOONSHOT_API_KEY',
  env_mgmt_key: 'VIBONOMICS_MOONSHOT_MGMT_KEY',
};

const PROVIDER: ProviderOut = {
  id: 7,
  type: 'moonshot',
  name: 'Kimi',
  enabled: true,
  base_url: 'https://gateway.example/v1',
  options: { region: 'cn' },
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
  balance: null,
  billing_url: null,
  balance_support: 'key',
  balance_note: '',
};

const SECOND: ProviderOut = { ...PROVIDER, id: 8, name: 'Kimi backup' };
const ROUTER: ProviderOut = { ...PROVIDER, id: 9, type: 'openrouter', name: 'OpenRouter', base_url: null, options: {} };

function json(body: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }),
  );
}

interface Served {
  providers?: ProviderOut[];
  /** Answers a request before the defaults do; return undefined to fall through. */
  override?: (url: string, init: RequestInit | undefined) => Promise<Response> | undefined;
}

function serve({ providers = [PROVIDER], override }: Served = {}) {
  fetchMock.mockImplementation((url: string, init?: RequestInit) => {
    const early = override?.(url, init);
    if (early !== undefined) {
      return early;
    }
    if (url === 'api/providers') {
      return json(providers);
    }
    if (url === 'api/providers/types') {
      return json([TYPE]);
    }
    if (url === 'api/providers/7/test') {
      return json({ ok: true, message: 'Key accepted.' });
    }
    if (url === 'api/providers/7' && init?.method === 'PUT') {
      return json(PROVIDER);
    }
    if (url === 'api/providers/refresh') {
      return json({ status: 'started' }, 202);
    }
    return json({ detail: `unexpected ${url}` }, 500);
  });
  vi.stubGlobal('fetch', fetchMock);
}

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MantineProvider>
        <ProvidersSection />
      </MantineProvider>
    </QueryClientProvider>,
  );
}

/** What the form last asked the server to test. */
function testedBody(): Record<string, unknown> {
  const sent = fetchMock.mock.calls.filter(([url]) => url === 'api/providers/7/test').at(-1);
  return JSON.parse((sent?.[1] as RequestInit).body as string) as Record<string, unknown>;
}

describe('ProvidersSection: testing an existing provider', () => {
  beforeEach(() => fetchMock.mockReset());
  afterEach(() => vi.unstubAllGlobals());

  it('tests a cleared Base URL and cleared options as cleared, since Save would store them that way', async () => {
    serve();
    const user = userEvent.setup();
    renderSection();
    await user.click(await screen.findByRole('button', { name: 'Edit' }));
    const form = within(await screen.findByRole('dialog', { name: 'Edit Kimi' }));

    await user.clear(form.getByLabelText('Base URL'));
    await user.clear(form.getByLabelText('Platform'));
    await user.click(form.getByRole('button', { name: 'Test' }));

    await waitFor(() => expect(testedBody()).toEqual({ base_url: '', options: {} }));
  });

  it('tests the Base URL as typed, trimmed', async () => {
    serve();
    const user = userEvent.setup();
    renderSection();
    await user.click(await screen.findByRole('button', { name: 'Edit' }));
    const form = within(await screen.findByRole('dialog', { name: 'Edit Kimi' }));

    await user.clear(form.getByLabelText('Base URL'));
    await user.type(form.getByLabelText('Base URL'), '  https://other.example/v1 ');
    await user.click(form.getByRole('button', { name: 'Test' }));

    await waitFor(() =>
      expect(testedBody()).toEqual({ base_url: 'https://other.example/v1', options: { region: 'cn' } }),
    );
  });
});

describe('ProvidersSection: the rows', () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.mocked(notifications.show).mockClear();
  });
  afterEach(() => vi.unstubAllGlobals());

  it('shows the result of each Test even when a second Test starts before the first finishes', async () => {
    let finishFirst: (response: Response) => void = () => undefined;
    const first = new Promise<Response>((resolve) => {
      finishFirst = resolve;
    });
    serve({
      providers: [PROVIDER, SECOND],
      override: (url) => {
        if (url === 'api/providers/7/test') {
          return first;
        }
        if (url === 'api/providers/8/test') {
          return json({ ok: true, message: 'Backup accepted.' });
        }
        return undefined;
      },
    });
    const user = userEvent.setup();
    renderSection();
    const [testFirst, testSecond] = await screen.findAllByRole('button', { name: 'Test' });

    await user.click(testFirst);
    await user.click(testSecond);
    await waitFor(() =>
      expect(notifications.show).toHaveBeenCalledWith({ message: 'Backup accepted.', color: 'green' }),
    );
    expect(testFirst).toHaveAttribute('data-loading', 'true');
    expect(testSecond).not.toHaveAttribute('data-loading');

    finishFirst(await json({ ok: false, message: 'Key rejected.' }));
    await waitFor(() => expect(notifications.show).toHaveBeenCalledWith({ message: 'Key rejected.', color: 'red' }));
    expect(testFirst).not.toHaveAttribute('data-loading');
  });

  it('does not carry a failed delete over to the next provider it is opened for', async () => {
    serve({
      providers: [PROVIDER, SECOND],
      override: (url, init) =>
        url === 'api/providers/7' && init?.method === 'DELETE'
          ? json({ detail: 'Kimi could not be deleted.' }, 500)
          : undefined,
    });
    const user = userEvent.setup();
    renderSection();
    const [deleteFirst, deleteSecond] = await screen.findAllByRole('button', { name: 'Delete' });

    await user.click(deleteFirst);
    const failed = within(await screen.findByRole('dialog', { name: 'Delete provider' }));
    await user.click(failed.getByRole('button', { name: 'Delete' }));
    expect(await failed.findByText('Kimi could not be deleted.')).toBeInTheDocument();
    await user.click(failed.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    await user.click(deleteSecond);
    const next = within(await screen.findByRole('dialog', { name: 'Delete provider' }));
    expect(next.getByText(/Delete Kimi backup\?/)).toBeInTheDocument();
    expect(next.queryByText('Kimi could not be deleted.')).toBeNull();
  });

  it('warns that deleting an OpenRouter provider also deletes its stored spend history', async () => {
    serve({ providers: [ROUTER] });
    const user = userEvent.setup();
    renderSection();
    await user.click(await screen.findByRole('button', { name: 'Delete' }));
    const dialog = within(await screen.findByRole('dialog', { name: 'Delete provider' }));
    expect(dialog.getByText(/spend history/)).toHaveTextContent(/30 days/);
  });

  it('does not mention spend history when the provider is not OpenRouter', async () => {
    serve();
    const user = userEvent.setup();
    renderSection();
    await user.click(await screen.findByRole('button', { name: 'Delete' }));
    const dialog = within(await screen.findByRole('dialog', { name: 'Delete provider' }));
    expect(dialog.getByText(/Delete Kimi\?/)).toBeInTheDocument();
    expect(dialog.queryByText(/spend history/)).toBeNull();
  });
});

describe('ProvidersSection: saving a provider', () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.mocked(notifications.show).mockClear();
  });
  afterEach(() => vi.unstubAllGlobals());

  async function saveKimi() {
    const user = userEvent.setup();
    renderSection();
    await user.click(await screen.findByRole('button', { name: 'Edit' }));
    const form = within(await screen.findByRole('dialog', { name: 'Edit Kimi' }));
    await user.click(form.getByRole('button', { name: 'Save' }));
  }

  it('says so when the refresh it asked for is queued behind one already running', async () => {
    serve({
      override: (url) => (url === 'api/providers/refresh' ? json({ status: 'queued' }, 202) : undefined),
    });
    await saveKimi();

    await waitFor(() =>
      expect(notifications.show).toHaveBeenCalledWith({
        message: 'Queued behind the refresh already running; it will run when that finishes.',
        color: 'blue',
      }),
    );
  });

  it('stays quiet about queueing when the refresh starts at once', async () => {
    serve();
    await saveKimi();

    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url === 'api/providers/refresh')).toBe(true));
    await new Promise((resolve) => setTimeout(resolve, 50)); // let the answer be read
    expect(notifications.show).toHaveBeenCalledWith({ message: 'Saved. Refreshing…', color: 'green' });
    expect(notifications.show).toHaveBeenCalledTimes(1);
  });
});
