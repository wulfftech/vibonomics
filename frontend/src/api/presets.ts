import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';
import { keys } from './keys';

/** What Vibonomics last wrote to the OpenRouter preset, from the local record only. */
export function usePresetState(enabled = true) {
  return useQuery({ queryKey: keys.presetState, queryFn: () => api.get('/api/presets/state'), enabled });
}

/** The dry run: reads from OpenRouter, ranks the models, writes nothing. */
export function usePresetPlan() {
  return useMutation({ mutationFn: () => api.post('/api/presets/plan') });
}

/** Writes the plan with this digest, if it is still the plan. */
export function useApplyPreset() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (digest: string) => api.post('/api/presets/apply', { body: { digest } }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.presetState });
    },
  });
}
