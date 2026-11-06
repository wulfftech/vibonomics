import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError, api } from './client';
import { keys } from './keys';
import type { SettingsIn } from './types';

export function useSettings() {
  return useQuery({ queryKey: keys.settings, queryFn: () => api.get('/api/settings') });
}

export function useSaveSettings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: SettingsIn) => api.put('/api/settings', { body }),
    // The body can carry benchmark keys, and a mutation lingers in the cache for five minutes by default.
    gcTime: 0,
    onSuccess: (saved) => {
      client.setQueryData(keys.settings, saved);
      void client.invalidateQueries({ queryKey: keys.models });
      void client.invalidateQueries({ queryKey: keys.system });
    },
    // Here and not in a `mutate` callback: those are dropped once the section that called it is gone, and the user
    // may have switched section, or been sent to the login card by a 401, before the save fails.
    onError: (error) =>
      notifications.show({
        color: 'red',
        message:
          error instanceof ApiError && error.status === 401
            ? 'You were signed out, so those changes were not saved. Log in and make them again.'
            : error.message,
      }),
  });
}
