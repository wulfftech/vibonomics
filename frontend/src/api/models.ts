import { keepPreviousData, useQuery } from '@tanstack/react-query';
import type { RouteQuery } from '../lib/routing';
import { api } from './client';
import { keys } from './keys';

export function useModels() {
  return useQuery({ queryKey: keys.models, queryFn: () => api.get('/api/models') });
}

/** An OpenRouter model's config snippets for a route other than OpenRouter's own; `routing` null skips the request. */
export function useModelSnippets(id: number, routing: RouteQuery | null) {
  return useQuery({
    queryKey: [...keys.model(id), 'snippets', routing] as const,
    queryFn: ({ signal }) =>
      api.get('/api/models/{model_pk}/snippets', { path: { model_pk: id }, query: routing ?? undefined, signal }),
    enabled: routing !== null,
    placeholderData: keepPreviousData,
  });
}

export function useModelDetail(id: number | null) {
  return useQuery({
    queryKey: keys.model(id ?? -1),
    queryFn: ({ signal }) => api.get('/api/models/{model_pk}', { path: { model_pk: id ?? -1 }, signal }),
    enabled: id !== null,
  });
}
