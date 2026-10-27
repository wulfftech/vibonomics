import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
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
