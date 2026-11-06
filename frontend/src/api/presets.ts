import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError, api } from './client';
import { keys } from './keys';
import type { AppSettings, PresetPlan, PresetSettings } from './types';

/** Is this the answer a visitor gets: the catalogue holds preset configs, which are not for visitors. */
export function isUnauthorized(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

/** What Vibonomics last wrote to each OpenRouter preset it manages, from the local record only. */
export function usePresetState(enabled = true) {
  return useQuery({ queryKey: keys.presetState, queryFn: () => api.get('/api/presets/state'), enabled });
}

/**
 * Every preset on the OpenRouter account as of the last sync. It reads the local copy and calls nothing; `enabled` is
 * false for a visitor, and a 401 is not retried, because asking again cannot change the answer.
 */
export function usePresetCatalog(enabled = true) {
  return useQuery({
    queryKey: keys.presetCatalog,
    queryFn: () => api.get('/api/presets/catalog'),
    enabled,
    retry: (count, error) => !isUnauthorized(error) && count < 2,
    // A sync that was started elsewhere (the timer, another tab) ends with an event, but the stream can be down.
    refetchInterval: (query) => (query.state.data?.syncing === true ? 2000 : false),
  });
}

/** Reads the account's presets now. The answer is the catalogue, so the page shows it without a second request. */
export function useSyncPresets() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post('/api/presets/sync'),
    onSuccess: (catalog) => {
      client.setQueryData(keys.presetCatalog, catalog);
    },
  });
}

/**
 * The dry run: reads from OpenRouter, ranks the models, writes nothing. `slug` plans the saved preset. `settings`
 * previews that preset's unsaved settings, so a change can be tried before it is kept; a plan made that way cannot be
 * applied.
 */
export function usePresetPlan() {
  return useMutation({
    mutationFn: (body: { slug: string } | { settings: PresetSettings }): Promise<PresetPlan> =>
      api.post('/api/presets/plan', { body }),
  });
}

/** Writes the plan with this digest to this saved preset, if it is still the plan. */
export function useApplyPreset() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { slug: string; digest: string }) => api.post('/api/presets/apply', { body }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.presetState });
      void client.invalidateQueries({ queryKey: keys.presetCatalog });
    },
  });
}

/** One preset on the account, managed or not: its config, the keys Vibonomics would keep, and whether it can be adopted. */
export function usePresetInspect(slug: string, enabled = true) {
  return useQuery({
    queryKey: keys.presetInspect(slug),
    queryFn: () => api.post('/api/presets/inspect', { body: { slug } }),
    enabled,
    retry: false,
    staleTime: 30_000,
  });
}

/**
 * Inspect as an action, for the adopt dialog. The dialog must adopt exactly what the owner read, so its answer is held
 * in the mutation, where a background refetch cannot swap the digest from under a ticked box.
 */
export function useInspectPreset() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (slug: string) => api.post('/api/presets/inspect', { body: { slug } }),
    onSuccess: (inspected, slug) => {
      client.setQueryData(keys.presetInspect(slug), inspected);
    },
  });
}

/** Adopts a preset made by hand. The answer is the item it adds to the settings, which the cached settings get now. */
export function useAdoptPreset() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { slug: string; digest: string }) => api.post('/api/presets/adopt', { body }),
    onSuccess: (item) => {
      client.setQueryData<{ settings: AppSettings } | undefined>(keys.settings, (old) => {
        if (old === undefined) {
          return old;
        }
        const presets = old.settings.presets ?? {};
        const items = presets.items ?? [];
        if (items.some((existing) => existing.slug === item.slug)) {
          return old;
        }
        return { ...old, settings: { ...old.settings, presets: { ...presets, items: [...items, item] } } };
      });
      void client.invalidateQueries({ queryKey: keys.presetCatalog });
      void client.invalidateQueries({ queryKey: keys.presetState });
    },
  });
}
