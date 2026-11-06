import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';
import { keys } from './keys';
import type { ProviderIn, ProviderTestOverride, ProviderUpdate } from './types';

export function useProviders() {
  return useQuery({ queryKey: keys.providers, queryFn: () => api.get('/api/providers') });
}

export function useProviderTypes() {
  return useQuery({
    queryKey: keys.providerTypes,
    queryFn: () => api.get('/api/providers/types'),
    staleTime: Infinity,
  });
}

function useInvalidateProviders() {
  const client = useQueryClient();
  return () => {
    void client.invalidateQueries({ queryKey: keys.providers });
    void client.invalidateQueries({ queryKey: keys.models });
    void client.invalidateQueries({ queryKey: keys.judges });
  };
}

// The mutations below carry typed keys as variables, and a mutation lingers in the cache for five minutes by
// default, so they are dropped as soon as nothing is watching them.

export function useCreateProvider() {
  const invalidate = useInvalidateProviders();
  return useMutation({
    mutationFn: (body: ProviderIn) => api.post('/api/providers', { body }),
    gcTime: 0,
    onSuccess: invalidate,
  });
}

export function useUpdateProvider() {
  const invalidate = useInvalidateProviders();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: ProviderUpdate }) =>
      api.put('/api/providers/{provider_id}', { path: { provider_id: id }, body }),
    gcTime: 0,
    onSuccess: invalidate,
  });
}

export function useDeleteProvider() {
  const invalidate = useInvalidateProviders();
  return useMutation({
    mutationFn: (id: number) => api.delete('/api/providers/{provider_id}', { path: { provider_id: id } }),
    onSuccess: invalidate,
  });
}

export function useTestProvider() {
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body?: ProviderTestOverride }) =>
      api.post('/api/providers/{provider_id}/test', { path: { provider_id: id }, body: body ?? {} }),
    gcTime: 0,
  });
}

/** Tests the values currently typed into the add-provider form, without saving them. */
export function useTestProviderDraft() {
  return useMutation({
    mutationFn: (body: ProviderIn) => api.post('/api/providers/test', { body }),
    gcTime: 0,
  });
}

export function useRefreshProvider() {
  const client = useQueryClient();
  return useMutation({
    // One request for the whole set: the server serialises refreshes per kind, so two concurrent
    // single-provider calls would drop one of them.
    mutationFn: (ids: number[]) => api.post('/api/providers/refresh', { body: { provider_ids: ids } }),
    onSuccess: () => {
      // The refresh runs in the background; the event stream invalidates when it finishes.
      void client.invalidateQueries({ queryKey: keys.refreshStatus });
    },
  });
}
