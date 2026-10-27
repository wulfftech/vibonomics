import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';
import { keys } from './keys';
import type { EvalRunIn } from './types';

const ACTIVE = new Set(['queued', 'running']);

export function isActiveRun(status: string): boolean {
  return ACTIVE.has(status);
}

export function useSuites(enabled = true) {
  return useQuery({ queryKey: keys.suites, queryFn: () => api.get('/api/evals/suites'), enabled, staleTime: 300_000 });
}

export function useJudges(enabled = true) {
  return useQuery({ queryKey: keys.judges, queryFn: () => api.get('/api/evals/judges'), enabled });
}

export function useRuns(enabled = true) {
  return useQuery({
    queryKey: keys.runs,
    queryFn: () => api.get('/api/evals/runs', { query: { limit: 100 } }),
    enabled,
    refetchInterval: (query) => (query.state.data?.some((run) => isActiveRun(run.status)) ? 5000 : false),
  });
}

export function useRun(id: number | null) {
  return useQuery({
    queryKey: keys.run(id ?? -1),
    queryFn: () => api.get('/api/evals/runs/{run_id}', { path: { run_id: id ?? -1 } }),
    enabled: id !== null,
    refetchInterval: (query) => (query.state.data && isActiveRun(query.state.data.run.status) ? 2000 : false),
  });
}

export function useStartRun() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: EvalRunIn) => api.post('/api/evals/runs', { body }),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.runs }),
  });
}

export function useCancelRun() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.post('/api/evals/runs/{run_id}/cancel', { path: { run_id: id } }),
    onSuccess: (_run, id) => {
      void client.invalidateQueries({ queryKey: keys.runs });
      void client.invalidateQueries({ queryKey: keys.run(id) });
    },
  });
}

export function useDeleteRun() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.delete('/api/evals/runs/{run_id}', { path: { run_id: id } }),
    onSuccess: (_void, id) => {
      client.removeQueries({ queryKey: keys.run(id) });
      void client.invalidateQueries({ queryKey: keys.runs });
      void client.invalidateQueries({ queryKey: keys.models });
    },
  });
}
