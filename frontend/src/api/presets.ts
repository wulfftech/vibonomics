import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';
import { keys } from './keys';
import type { PresetSettings } from './types';

/** What Vibonomics last wrote to the OpenRouter preset, from the local record only. */
export function usePresetState(enabled = true) {
  return useQuery({ queryKey: keys.presetState, queryFn: () => api.get('/api/presets/state'), enabled });
}

/** The account's own presets, to start a new one from. Reads OpenRouter, so it asks once a minute at most. */
export function usePresetList() {
  return useQuery({
    queryKey: keys.presetList,
    queryFn: () => api.post('/api/presets/list'),
    staleTime: 60_000,
    retry: false,
  });
}

/**
 * The dry run: reads from OpenRouter, ranks the models, writes nothing. With `settings` it previews those, unsaved,
 * so a change can be tried before it is kept; a plan made that way cannot be applied.
 */
export function usePresetPlan() {
  return useMutation({
    mutationFn: (settings?: PresetSettings) =>
      api.post('/api/presets/plan', { body: settings === undefined ? {} : { settings } }),
  });
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
