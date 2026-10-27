import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';
import { keys } from './keys';

/** Claude subscription usage pushed from Claude Code (plus login-token data via the Anthropic provider). */
export function useClaudeSubscription(enabled = true) {
  return useQuery({
    queryKey: keys.claudeSubscription,
    queryFn: () => api.get('/api/subscription/claude'),
    enabled,
    staleTime: 60_000,
  });
}

export function useRotateClaudeToken() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post('/api/subscription/claude/rotate'),
    onSuccess: (data) => {
      client.setQueryData(keys.claudeSubscription, data);
    },
  });
}

export function useClearClaudeSnapshot() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.delete('/api/subscription/claude'),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.claudeSubscription });
      void client.invalidateQueries({ queryKey: keys.providers });
    },
  });
}
