import { Card, Flex, Stack, Text, Title } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { useMemo, useState, type ReactNode } from 'react';
import { isUnauthorized, usePresetCatalog, usePresetInspect, usePresetState, useSyncPresets } from '../../api/presets';
import { useCanChange } from '../../api/system';
import { fullSettings, PRESET_DEFAULTS, type FullSettings, type PresetSettings } from '../../api/types';
import { ErrorState, LoadingState } from '../../components/DataState';
import { usePersistentState } from '../../lib/usePersistentState';
import { GLOBAL_KEY, hasGuardrailProblems, itemSlug, presetEntries, presetSlug } from '../../lib/presets';
import { AdoptPresetModal } from './AdoptPresetModal';
import { GlobalPresetSettings } from './GlobalPresetSettings';
import { NewPresetModal } from './NewPresetModal';
import { PresetEditor } from './PresetEditor';
import { PresetInspector } from './PresetInspector';
import { PresetsPanel, type CatalogView } from './PresetsPanel';
import { SaveBar } from './SaveBar';
import type { TemplateSource } from './TemplatePicker';
import { usePresetUpdate } from './usePresetUpdate';
import { useSettingsDraft } from './useSettingsDraft';

/** Vibonomics keeps at most this many presets; the backend refuses more. */
const MAX_PRESETS = 20;

type Presets = FullSettings['presets'];

/** Preset configs are the owner's, so a visitor to an instance that is open to read gets no panel and no catalogue. */
function LogInNote() {
  return (
    <Card withBorder padding="md">
      <Text size="sm">Log in to manage presets.</Text>
    </Card>
  );
}

/** Settings that apply to OpenRouter alone: the presets Vibonomics keeps pointed at the best-value models. */
export function OpenRouterSection() {
  const canChange = useCanChange();
  const draft = useSettingsDraft(['presets']);
  const catalog = usePresetCatalog(canChange);
  const state = usePresetState(canChange);
  const update = usePresetUpdate();
  const sync = useSyncPresets();
  const [selected, setSelected] = usePersistentState<string>('vibonomics.presets.selected', GLOBAL_KEY);
  const [adopting, setAdopting] = useState('');
  const [adoptOpen, adoptModal] = useDisclosure(false);
  const [newOpen, newModal] = useDisclosure(false);

  const presets = draft.value?.presets;
  const savedItems = useMemo(
    () => (draft.query.data ? fullSettings(draft.query.data.settings).presets.items : []),
    [draft.query.data],
  );
  const entries = useMemo(
    () => presetEntries(catalog.data?.presets ?? [], presets?.items ?? []),
    [catalog.data?.presets, presets?.items],
  );
  const unsaved = useMemo(() => {
    const was = new Map(savedItems.map((item) => [itemSlug(item), JSON.stringify(item)]));
    return new Set(
      (presets?.items ?? []).filter((item) => was.get(itemSlug(item)) !== JSON.stringify(item)).map(itemSlug),
    );
  }, [savedItems, presets?.items]);

  const entry = selected === GLOBAL_KEY ? null : (entries.find((e) => e.slug === selected) ?? null);
  const inspected = entry !== null && entry.item === null && entry.catalog !== null ? entry.slug : '';
  const inspect = usePresetInspect(inspected, inspected !== '');

  if (!canChange || (catalog.isError && isUnauthorized(catalog.error))) {
    return <LogInNote />;
  }
  if (draft.query.isPending) {
    return <LoadingState />;
  }
  if (draft.query.isError || draft.value === null || presets === undefined) {
    return <ErrorState message={draft.query.error?.message} />;
  }
  const { scoring } = draft.value;

  const setPresets = (change: (old: Presets) => Presets) =>
    draft.update((old) => ({ ...old, presets: change(old.presets) }));
  const addItem = (item: PresetSettings) => {
    setPresets((old) => ({ ...old, items: [...old.items.filter((i) => itemSlug(i) !== itemSlug(item)), item] }));
    setSelected(itemSlug(item));
  };

  const catalogView: CatalogView = {
    data: catalog.data,
    loading: catalog.isPending,
    failure: catalog.isError ? catalog.error.message : null,
  };
  const templates: TemplateSource = {
    presets: catalog.data?.presets ?? [],
    problem: catalog.isError
      ? catalog.error.message
      : catalog.data !== undefined && catalog.data.presets.length === 0
        ? catalog.data.error
        : null,
    loading: catalog.isPending,
  };

  const item = entry?.item ?? null;
  const pushesSaved = entry !== null && item !== null && item.enabled && unsaved.has(entry.slug);
  // Save writes every preset in the draft, so a guardrail left unfinished on any of them holds it back.
  const unfinished = presets.items.filter((i) => hasGuardrailProblems(i.guardrails)).map(itemSlug);
  const saveBar = (
    <Stack gap="xs">
      {pushesSaved && unfinished.length === 0 && (
        <Text size="xs" c="dimmed" ta="right">
          Saving also sends the update to OpenRouter.
        </Text>
      )}
      {unfinished.length > 0 && (
        <Text size="xs" c="dimmed" ta="right">
          Save is off until the guardrails of {unfinished.join(', ')} have a usable value, or are set to Leave or
          Remove.
        </Text>
      )}
      <SaveBar
        dirty={draft.dirty}
        canSave={unfinished.length === 0}
        saving={draft.saving}
        onReset={draft.reset}
        onSave={() => draft.submit({ onSaved: pushesSaved ? () => update.pushSaved(entry.slug) : undefined })}
      />
    </Stack>
  );

  let pane: ReactNode;
  if (entry === null) {
    pane =
      selected !== GLOBAL_KEY && catalog.isPending ? (
        <LoadingState />
      ) : (
        <GlobalPresetSettings
          presets={presets}
          onChange={(patch) => setPresets((old) => ({ ...old, ...patch }))}
          saveBar={saveBar}
        />
      );
  } else if (item !== null) {
    pane = (
      <PresetEditor
        entry={entry}
        item={item}
        scoring={scoring}
        templates={templates}
        unsaved={unsaved.has(entry.slug)}
        last={state.data?.slugs[entry.slug]}
        update={update}
        onChange={(patch) =>
          setPresets((old) => ({
            ...old,
            items: old.items.map((i) => (itemSlug(i) === entry.slug ? { ...i, ...patch } : i)),
          }))
        }
        onStop={() => {
          setPresets((old) => ({ ...old, items: old.items.filter((i) => itemSlug(i) !== entry.slug) }));
          setSelected(GLOBAL_KEY);
          notifications.show({ message: `${entry.slug} stops being managed once you save.`, color: 'blue' });
        }}
        saveBar={saveBar}
      />
    );
  } else if (entry.catalog !== null) {
    const preset = entry.catalog;
    pane = (
      <Stack gap="lg">
        <PresetInspector
          entry={entry}
          preset={preset}
          inspect={inspect}
          onAdopt={() => {
            setAdopting(entry.slug);
            adoptModal.open();
          }}
          onManageAgain={
            preset.managed && entry.slug.startsWith('vibonomics-')
              ? () => addItem({ ...PRESET_DEFAULTS, slug: entry.slug })
              : null
          }
        />
        {draft.dirty && saveBar}
      </Stack>
    );
  }

  return (
    <Stack gap="lg">
      <Stack gap={4}>
        <Title order={3}>OpenRouter</Title>
        <Text size="sm" c="dimmed">
          Settings that apply to OpenRouter only. Its keys and models are under All providers.
        </Text>
        <Text size="sm" c="dimmed">
          Keeps OpenRouter presets pointed at the models that give the most score per dollar, so any client that cannot
          pin a provider can use <code>@preset/name</code> as its model and follow the best value. Each update is a new
          version of the preset. Vibonomics only writes a preset it created itself or that you adopted, and never
          deletes one.
        </Text>
      </Stack>

      <Flex direction={{ base: 'column', md: 'row' }} gap="lg" align={{ base: 'stretch', md: 'flex-start' }}>
        <PresetsPanel
          entries={entries}
          selected={entry === null ? GLOBAL_KEY : entry.slug}
          onSelect={setSelected}
          catalog={catalogView}
          refreshing={sync.isPending}
          onRefresh={() => sync.mutate()}
          onNew={newModal.open}
          canAdd={presets.items.length < MAX_PRESETS}
          unsaved={unsaved}
        />
        <Stack gap="lg" style={{ flex: 1, minWidth: 0 }}>
          {pane}
        </Stack>
      </Flex>

      <NewPresetModal
        opened={newOpen}
        onClose={newModal.close}
        templates={templates}
        taken={new Set(presets.items.map(itemSlug))}
        onAccount={new Set((catalog.data?.presets ?? []).map((p) => p.slug))}
        onCreate={(name, template) => {
          addItem({ ...PRESET_DEFAULTS, slug: presetSlug(name), template_slug: template });
          newModal.close();
        }}
      />
      <AdoptPresetModal
        slug={adopting}
        opened={adoptOpen}
        onClose={adoptModal.close}
        onAdopted={(adopted) => {
          // The adopt call put the item in the cached settings; the draft needs it too, or the next Save would drop it
          // and so hand the preset back.
          addItem({ ...PRESET_DEFAULTS, ...adopted });
        }}
      />
    </Stack>
  );
}
