import { createContext, useContext } from 'react';
import type { RefreshKind } from './types';

export interface EventStreamState {
  connected: boolean;
  /** The latest progress message per running refresh kind (e.g. the provider being fetched). */
  progress: Partial<Record<string, string>>;
  /** Marks kinds the user asked for in this tab, so their completion toasts even when all went well. */
  markRequested: (kinds: RefreshKind[]) => void;
}

export const EventStreamContext = createContext<EventStreamState>({
  connected: true,
  progress: {},
  markRequested: () => undefined,
});

export function useEventStream(): EventStreamState {
  return useContext(EventStreamContext);
}
