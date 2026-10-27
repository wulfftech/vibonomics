import { useCallback, useEffect, useState } from 'react';

/**
 * `useState` backed by localStorage. Stored objects are merged over the default, so a field added
 * later gets its default instead of `undefined`; `sanitize` can repair anything stale.
 */
export function usePersistentState<T>(
  key: string,
  initial: T,
  sanitize: (value: T) => T = (value) => value,
): [T, (update: T | ((old: T) => T)) => void] {
  const [state, setState] = useState<T>(() => {
    try {
      const raw = window.localStorage.getItem(key);
      if (raw === null) {
        return initial;
      }
      const parsed: unknown = JSON.parse(raw);
      if (typeof initial === 'object' && initial !== null && !Array.isArray(initial)) {
        if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
          return initial;
        }
        return sanitize({ ...initial, ...(parsed as Partial<T>) });
      }
      return sanitize(parsed as T);
    } catch {
      return initial;
    }
  });

  useEffect(() => {
    try {
      window.localStorage.setItem(key, JSON.stringify(state));
    } catch {
      // Private mode or a full quota: the state still works for this session.
    }
  }, [key, state]);

  const update = useCallback((next: T | ((old: T) => T)) => setState(next), []);
  return [state, update];
}
