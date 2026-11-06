import { notifications } from '@mantine/notifications';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from './client';
import { useSaveSettings } from './settings';
import type { SettingsIn } from './types';

const mocks = vi.hoisted(() => ({ put: vi.fn<(path: string, options?: unknown) => Promise<unknown>>() }));
vi.mock('@mantine/notifications', () => ({ notifications: { show: vi.fn() } }));
vi.mock('./client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./client')>()),
  api: { get: vi.fn(), put: mocks.put },
}));

let client: QueryClient;

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

const body = { settings: {}, benchmark_keys: { artificial_analysis: 'aa-not-a-real-key' } } as SettingsIn;

beforeEach(() => {
  mocks.put.mockReset();
  vi.mocked(notifications.show).mockReset();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});

describe('useSaveSettings', () => {
  it('does not keep the typed benchmark keys in the mutation cache once the section is gone', async () => {
    mocks.put.mockResolvedValue({ settings: {} });
    const { result, unmount } = renderHook(() => useSaveSettings(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync(body);
    });
    unmount();
    await waitFor(() => expect(client.getMutationCache().getAll()).toHaveLength(0));
  });

  it('reports a failed save even when the section that asked for it has been left', async () => {
    let fail: (error: Error) => void = () => undefined;
    mocks.put.mockReturnValue(new Promise((_resolve, reject) => (fail = reject)));
    const { result, unmount } = renderHook(() => useSaveSettings(), { wrapper });
    act(() => result.current.mutate(body));
    unmount();
    await act(async () => {
      fail(new ApiError(422, 'instance_name: must not be blank'));
      await Promise.resolve();
    });
    await waitFor(() =>
      expect(notifications.show).toHaveBeenCalledWith({ color: 'red', message: 'instance_name: must not be blank' }),
    );
  });

  it('says the changes were lost when the session ended under the save', async () => {
    mocks.put.mockRejectedValue(new ApiError(401, 'Not authenticated'));
    const { result } = renderHook(() => useSaveSettings(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync(body).catch(() => undefined);
    });
    expect(notifications.show).toHaveBeenCalledTimes(1);
    expect(vi.mocked(notifications.show).mock.calls[0]?.[0]).toMatchObject({
      color: 'red',
      message: expect.stringMatching(/not saved/i) as string,
    });
  });
});
