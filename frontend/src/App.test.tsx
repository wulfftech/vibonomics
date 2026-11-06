import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthGate } from './App';
import { keys } from './api/keys';

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock('./api/client', () => ({
  api: { get: mocks.get, post: mocks.post },
  setUnauthorizedListener: vi.fn(),
}));
// The shell and the pages fetch their own data; only the gate is under test.
vi.mock('./components/AppLayout', () => ({ AppLayout: () => <div>app shell</div> }));
vi.mock('./pages/ModelsPage', () => ({ ModelsPage: () => null }));
vi.mock('./pages/UsagePage', () => ({ UsagePage: () => null }));
vi.mock('./pages/SettingsPage', () => ({ SettingsPage: () => null }));

const open = { enabled: false, authenticated: false, open_read: false };
const lockedOut = { enabled: true, authenticated: false, open_read: false };
let client: QueryClient;

function mount() {
  render(
    <MantineProvider>
      <QueryClientProvider client={client}>
        <AuthGate />
      </QueryClientProvider>
    </MantineProvider>,
  );
}

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  mocks.get.mockReset();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});

afterEach(() => {
  vi.useRealTimers();
});

describe('AuthGate', () => {
  it('recovers on its own when the page was loaded while the server was restarting', async () => {
    mocks.get.mockRejectedValueOnce(new Error('Could not reach the Vibonomics server.'));
    mocks.get.mockResolvedValue(open);
    mount();
    await advance(100);
    expect(screen.getByText('Vibonomics is unreachable')).toBeInTheDocument();

    await advance(5000);
    expect(screen.queryByText('Vibonomics is unreachable')).not.toBeInTheDocument();
    expect(screen.getByText('app shell')).toBeInTheDocument();
  });

  it('keeps the pages when a later status check fails, so a blip cannot throw away unsaved edits', async () => {
    mocks.get.mockResolvedValue(open);
    mount();
    await advance(100);
    expect(screen.getByText('app shell')).toBeInTheDocument();

    mocks.get.mockRejectedValue(new Error('Could not reach the Vibonomics server.'));
    await act(async () => {
      await client.invalidateQueries({ queryKey: keys.auth });
    });
    await advance(10);
    expect(screen.getByText('app shell')).toBeInTheDocument();
    expect(screen.queryByText('Vibonomics is unreachable')).not.toBeInTheDocument();
  });

  it('shows the login card when a later status check says the session is gone', async () => {
    mocks.get.mockResolvedValue({ enabled: true, authenticated: true, open_read: false });
    mount();
    await advance(100);
    expect(screen.getByText('app shell')).toBeInTheDocument();

    mocks.get.mockResolvedValue(lockedOut);
    await act(async () => {
      await client.invalidateQueries({ queryKey: keys.auth });
    });
    await advance(10);
    expect(screen.queryByText('app shell')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Password')).toBeInTheDocument();
  });
});
