import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { api } from './client';
import { keys } from './keys';

export interface SpendQuery {
  days: number;
  providerId: number | null;
  key: string | null;
}

/** Spend, tokens and cost per token from the stored history. A change of filter keeps the old frame until the new one lands. */
export function useSpend({ days, providerId, key }: SpendQuery) {
  return useQuery({
    queryKey: keys.spend(days, providerId, key),
    queryFn: () =>
      api.get('/api/usage', { query: { days, provider_id: providerId ?? undefined, key: key ?? undefined } }),
    placeholderData: keepPreviousData,
  });
}
