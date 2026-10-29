import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { canChange } from '../lib/auth';
import { api } from './client';
import { keys } from './keys';
import type { RefreshKind } from './types';

export function useAuthStatus() {
  return useQuery({ queryKey: keys.auth, queryFn: () => api.get('/api/auth/status') });
}

export function useLogin() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (password: string) => api.post('/api/auth/login', { body: { password } }),
    onSuccess: (status) => {
      client.setQueryData(keys.auth, status);
      void client.invalidateQueries({ predicate: (query) => query.queryKey[0] !== 'auth' });
    },
  });
}

export function useLogout() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post('/api/auth/logout'),
    onSuccess: (status) => {
      client.setQueryData(keys.auth, status);
      // What a visitor may read differs (key hints, the ingest token), so fetch it all again.
      void client.invalidateQueries({ predicate: (query) => query.queryKey[0] !== 'auth' });
    },
  });
}

/** Whether this browser may change things; false for a visitor of an instance that is open to read. */
export function useCanChange(): boolean {
  return canChange(useAuthStatus().data);
}

export function useSystemStatus() {
  return useQuery({ queryKey: keys.system, queryFn: () => api.get('/api/system/status'), staleTime: 60_000 });
}

export function useRefreshStatus() {
  return useQuery({ queryKey: keys.refreshStatus, queryFn: () => api.get('/api/refresh/status') });
}

export function useTriggerRefresh() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (kinds: RefreshKind[]) => api.post('/api/refresh', { body: { kinds } }),
    onSuccess: (status) => {
      client.setQueryData(keys.refreshStatus, (old: typeof status | undefined) => ({
        running: [...new Set([...(old?.running ?? []), ...status.running])],
        last: old?.last ?? {},
      }));
    },
  });
}
