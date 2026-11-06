import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { act, render, renderHook, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { keys } from './keys';
import { useAuthStatus, useCanChange, useLogin, useLogout } from './system';

const mocks = vi.hoisted(() => ({
  get: vi.fn<(path: string) => Promise<unknown>>(),
  post: vi.fn<(path: string, options?: unknown) => Promise<unknown>>(),
}));
vi.mock('./client', () => ({ api: { get: mocks.get, post: mocks.post } }));

let client: QueryClient;

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  mocks.get.mockReset();
  mocks.post.mockReset();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});

afterEach(() => {
  vi.useRealTimers();
});

describe('useAuthStatus', () => {
  it('asks again every few seconds while the server cannot be reached, and recovers by itself', async () => {
    vi.useFakeTimers();
    mocks.get.mockRejectedValueOnce(new Error('Could not reach the Vibonomics server.'));
    mocks.get.mockResolvedValue({ enabled: false, authenticated: false, open_read: false });
    const { result } = renderHook(() => useAuthStatus(), { wrapper });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(result.current.isError).toBe(true);
    expect(mocks.get).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(result.current.isError).toBe(false);
    expect(result.current.data).toEqual({ enabled: false, authenticated: false, open_read: false });

    // Once it answers it stops asking.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(mocks.get).toHaveBeenCalledTimes(2);
  });
});

describe('useLogin', () => {
  it('does not keep the typed password in the mutation cache once the card is gone', async () => {
    mocks.post.mockResolvedValue({ enabled: true, authenticated: true, open_read: false });
    const { result, unmount } = renderHook(() => useLogin(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync('hunter2-not-real');
    });
    unmount();
    await waitFor(() => expect(client.getMutationCache().getAll()).toHaveLength(0));
  });
});

describe('useLogout', () => {
  const user = { enabled: true, authenticated: true, open_read: true };
  const visitor = { enabled: true, authenticated: false, open_read: true };
  const modelCalls = () => mocks.get.mock.calls.filter(([path]) => path === '/api/models').length;
  const presetCalls = () => mocks.post.mock.calls.filter(([path]) => path === '/api/presets/list').length;

  /** The preset list is asked for by a panel that, like the OpenRouter settings, goes when the user does. */
  function PresetPanel() {
    useQuery({ queryKey: keys.presetList, queryFn: () => mocks.post('/api/presets/list') });
    return <div>preset panel</div>;
  }

  /** A page that is still mounted when the logout lands, like the Models page of an instance that is open to read. */
  function Page({ onLogout }: { onLogout: (logout: () => void) => void }) {
    const canChange = useCanChange();
    useQuery({ queryKey: keys.models, queryFn: () => mocks.get('/api/models') });
    const logout = useLogout();
    onLogout(() => logout.mutate());
    return canChange ? <PresetPanel /> : <div>login card</div>;
  }

  function mountPage() {
    let logout: () => void = () => undefined;
    render(
      <QueryClientProvider client={client}>
        <Page onLogout={(fn) => (logout = fn)} />
      </QueryClientProvider>,
    );
    return { logout: () => act(() => logout()) };
  }

  beforeEach(() => {
    mocks.get.mockImplementation((path: string) => Promise.resolve(path === '/api/auth/status' ? user : []));
    mocks.post.mockImplementation((path: string) =>
      Promise.resolve(path === '/api/auth/logout' ? visitor : { presets: [] }),
    );
  });

  it('refetches the pages that stay, but not the POST-backed preset list that a visitor cannot make', async () => {
    // Fetched as the user and not on screen now, so the visitor must not be handed it from the cache later.
    client.setQueryData(keys.settings, { settings: { secret_hint: '...1234' } });
    const page = mountPage();
    await screen.findByText('preset panel');
    await waitFor(() => expect(client.getQueryData(keys.presetList)).toBeDefined());
    expect(modelCalls()).toBe(1);
    expect(presetCalls()).toBe(1);

    page.logout();
    await screen.findByText('login card');

    await waitFor(() => expect(modelCalls()).toBe(2));
    expect(presetCalls()).toBe(1);
    expect(client.getQueryData(keys.settings)).toBeUndefined();
  });

  it('keeps a page that stays mounted listening to later invalidations', async () => {
    const page = mountPage();
    await waitFor(() => expect(modelCalls()).toBe(1));
    page.logout();
    await waitFor(() => expect(modelCalls()).toBe(2));

    // The event stream's invalidations still reach it.
    await act(async () => {
      await client.invalidateQueries({ queryKey: keys.models });
    });
    expect(modelCalls()).toBe(3);
  });
});
