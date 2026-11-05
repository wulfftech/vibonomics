import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ProviderOut, ProviderTypeOut } from '../../api/types';
import { ProvidersSection } from './ProvidersSection';

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

function serve() {
  fetchMock.mockImplementation((url: string) => {
    const json = (body: unknown) =>
      Promise.resolve(
        new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } }),
      );
    if (url === 'api/providers') {
      return json([PROVIDER]);
    }
    if (url === 'api/providers/types') {
      return json([TYPE]);
    }
    if (url === 'api/providers/7/test') {
      return json({ ok: true, message: 'Key accepted.' });
    }
    return Promise.resolve(new Response(JSON.stringify({ detail: `unexpected ${url}` }), { status: 500 }));
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
