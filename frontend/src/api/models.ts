import { useQuery } from '@tanstack/react-query';
import { api } from './client';
import { keys } from './keys';

export function useModels() {
  return useQuery({ queryKey: keys.models, queryFn: () => api.get('/api/models') });
}

export function useModelDetail(id: number | null) {
  return useQuery({
    queryKey: keys.model(id ?? -1),
    queryFn: ({ signal }) => api.get('/api/models/{model_pk}', { path: { model_pk: id ?? -1 }, signal }),
    enabled: id !== null,
  });
}
