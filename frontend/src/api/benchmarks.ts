import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';
import { keys } from './keys';

export function useUnmatchedRows(enabled: boolean) {
  return useQuery({
    queryKey: keys.benchmarkRows,
    queryFn: () => api.get('/api/benchmarks/rows', { query: { unmatched_only: true } }),
    enabled,
  });
}

export function useCanonicalSearch(q: string, enabled: boolean) {
  return useQuery({
    queryKey: keys.canonical(q),
    queryFn: ({ signal }) => api.get('/api/benchmarks/canonical', { query: { q, limit: 30 }, signal }),
    enabled,
    staleTime: 5 * 60_000,
    placeholderData: keepPreviousData,
  });
}

export function useSetAlias() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { source: string; source_model: string; canonical_id: string | null }) =>
      api.put('/api/benchmarks/aliases', { body }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.benchmarkRows });
      void client.invalidateQueries({ queryKey: keys.settings });
      void client.invalidateQueries({ queryKey: keys.models });
    },
  });
}
