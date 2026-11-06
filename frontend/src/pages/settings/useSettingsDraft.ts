import { notifications } from '@mantine/notifications';
import { useCallback, useMemo, useState } from 'react';
import { useSaveSettings, useSettings } from '../../api/settings';
import { fullSettings, type FullSettings, type SettingsSectionKey } from '../../api/types';

/**
 * For a section that edits only some fields of a settings section: which ones. Everything else in that section is
 * left to the other tabs, so a stale copy of it in this draft can neither show as an edit nor be saved over them.
 */
export type OwnedFields = { [K in SettingsSectionKey]?: ReadonlyArray<keyof FullSettings[K]> };

function owned(section: object, fields: ReadonlyArray<PropertyKey>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(section).filter(([name]) => fields.includes(name)));
}

/**
 * A local draft of the settings document for one Settings section. Saving PUTs the latest server
 * copy with only `keys` replaced from the draft, so sections never overwrite each other. `fields` narrows that to
 * single fields where a section edits only part of one of its `keys`.
 */
export function useSettingsDraft(keys: SettingsSectionKey[], fields: OwnedFields = {}) {
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

  const editable = (key: SettingsSectionKey, settings: FullSettings): object => {
    const only = fields[key];
    return only === undefined ? settings[key] : owned(settings[key], only);
  };

  const dirty =
    draft !== null &&
    server !== null &&
    keys.some((key) => JSON.stringify(editable(key, draft)) !== JSON.stringify(editable(key, server)));

  const submit = (options: { benchmarkKeys?: Record<string, string | null>; onSaved?: () => void } = {}) => {
    if (query.data === undefined || value === null) {
      return;
    }
    const latest = fullSettings(query.data.settings);
    const merged: FullSettings = { ...latest };
    for (const key of keys) {
      const only = fields[key];
      Object.assign(merged, {
        [key]: only === undefined ? value[key] : { ...latest[key], ...owned(value[key], only) },
      });
    }
    save.mutate(
      { settings: merged, benchmark_keys: options.benchmarkKeys ?? {} },
      {
        onSuccess: () => {
          setDraft(null);
          options.onSaved?.();
          notifications.show({ message: 'Saved', color: 'green' });
        },
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
