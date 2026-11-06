import { partialMatchKey, useMutation, useQuery, useQueryClient, type Query } from '@tanstack/react-query';
import { canChange } from '../lib/auth';
import { api } from './client';
import { keys } from './keys';
import type { RefreshKind } from './types';

/** How long to wait before asking again after the auth status could not be fetched. */
const UNREACHABLE_RETRY_MS = 5000;

export function useAuthStatus() {
  return useQuery({
    queryKey: keys.auth,
    queryFn: () => api.get('/api/auth/status'),
    // A page loaded during a restart would otherwise stay on "unreachable" until someone reloads it.
    refetchInterval: (query) => (query.state.status === 'error' ? UNREACHABLE_RETRY_MS : false),
  });
}

export function useLogin() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (password: string) => api.post('/api/auth/login', { body: { password } }),
    // The password is the variable; a mutation lingers in the cache for five minutes by default.
    gcTime: 0,
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
      // What a visitor may read differs (key hints, the ingest token), so nothing fetched as the user may stay. Pages
      // that are still mounted refetch as the visitor. Removing a query that is still observed would leave its page
      // deaf to the event stream's invalidations, so only the preset list is dropped while mounted: it is a POST a
      // visitor cannot make, and the panel that asks for it is replaced by the login card on this very render.
      const notAuth = (query: Query) => query.queryKey[0] !== 'auth';
      client.removeQueries({
        predicate: (query) =>
          notAuth(query) && (query.getObserversCount() === 0 || partialMatchKey(query.queryKey, keys.presetList)),
      });
      void client.invalidateQueries({ predicate: notAuth });
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
