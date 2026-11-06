import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useCreateProvider, useTestProvider, useTestProviderDraft, useUpdateProvider } from './providers';

const mocks = vi.hoisted(() => ({
  post: vi.fn<(path: string, options?: unknown) => Promise<unknown>>(),
  put: vi.fn<(path: string, options?: unknown) => Promise<unknown>>(),
}));
vi.mock('./client', () => ({ api: { get: vi.fn(), post: mocks.post, put: mocks.put, delete: vi.fn() } }));

let client: QueryClient;

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  mocks.post.mockReset();
  mocks.put.mockReset();
  mocks.post.mockResolvedValue({ ok: true });
  mocks.put.mockResolvedValue({ ok: true });
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});

const FAKE_KEY = 'sk-not-a-real-key';

describe('provider mutations that carry keys', () => {
  it.each([
    ['saving a new provider', () => useCreateProvider(), { type: 'openai', enabled: true, api_key: FAKE_KEY }],
    ['updating a provider', () => useUpdateProvider(), { id: 1, body: { api_key: FAKE_KEY } }],
    ['testing a provider', () => useTestProvider(), { id: 1, body: { api_key: FAKE_KEY } }],
    [
      'testing the add-provider form',
      () => useTestProviderDraft(),
      { type: 'openai', enabled: true, api_key: FAKE_KEY },
    ],
  ] as const)('%s leaves nothing in the mutation cache once the form is gone', async (_name, useHook, variables) => {
    const { result, unmount } = renderHook(() => useHook(), { wrapper });
    await act(async () => {
      // The hooks take different variables; each row above pairs a hook with its own.
      await (result.current.mutateAsync as (vars: unknown) => Promise<unknown>)(variables);
    });
    expect(client.getMutationCache().getAll()).toHaveLength(1);
    unmount();
    await waitFor(() => expect(client.getMutationCache().getAll()).toHaveLength(0));
  });
});
