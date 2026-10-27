import { notifications } from '@mantine/notifications';
import { useCallback, useMemo, useState } from 'react';
import { useSaveSettings, useSettings } from '../../api/settings';
import { fullSettings, type FullSettings, type SettingsSectionKey } from '../../api/types';

/**
 * A local draft of the settings document for one Settings section. Saving PUTs the latest server
 * copy with only `keys` replaced from the draft, so sections never overwrite each other.
 */
export function useSettingsDraft(keys: SettingsSectionKey[]) {
  const query = useSettings();
  const save = useSaveSettings();
  const server = useMemo(() => (query.data ? fullSettings(query.data.settings) : null), [query.data]);
  const [draft, setDraft] = useState<FullSettings | null>(null);
  const value = draft ?? server;

  const update = useCallback(
    (fn: (old: FullSettings) => FullSettings) =>
      setDraft((old) => {
        const base = old ?? server;
        return base === null ? old : fn(base);
      }),
    [server],
  );

  const dirty =
    draft !== null && server !== null && keys.some((key) => JSON.stringify(draft[key]) !== JSON.stringify(server[key]));

  const submit = (options: { benchmarkKeys?: Record<string, string | null>; onSaved?: () => void } = {}) => {
    if (query.data === undefined || value === null) {
      return;
    }
    const latest = fullSettings(query.data.settings);
    const merged: FullSettings = { ...latest };
    for (const key of keys) {
      Object.assign(merged, { [key]: value[key] });
    }
    save.mutate(
      { settings: merged, benchmark_keys: options.benchmarkKeys ?? {} },
      {
        onSuccess: () => {
          setDraft(null);
          options.onSaved?.();
          notifications.show({ message: 'Saved', color: 'green' });
        },
        onError: (error) => notifications.show({ message: error.message, color: 'red' }),
      },
    );
  };

  return {
    query,
    value,
    update,
    dirty,
    submit,
    saving: save.isPending,
    reset: () => setDraft(null),
  };
}

/** NumberInput hands back `string` while the field is mid-edit or empty. */
export function toNumber(value: number | string, fallback = 0): number {
  if (typeof value === 'number') {
    return value;
  }
  const parsed = Number(value);
  return value.trim() === '' || Number.isNaN(parsed) ? fallback : parsed;
}
