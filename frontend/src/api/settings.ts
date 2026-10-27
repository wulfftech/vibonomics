import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';
import { keys } from './keys';
import type { SettingsIn } from './types';

export function useSettings() {
  return useQuery({ queryKey: keys.settings, queryFn: () => api.get('/api/settings') });
}

export function useSaveSettings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: SettingsIn) => api.put('/api/settings', { body }),
    onSuccess: (saved) => {
      client.setQueryData(keys.settings, saved);
      void client.invalidateQueries({ queryKey: keys.models });
      void client.invalidateQueries({ queryKey: keys.system });
    },
  });
}
