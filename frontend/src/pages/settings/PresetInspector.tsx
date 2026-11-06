import { Alert, Button, Card, Group, Stack, Text, Title } from '@mantine/core';
import type { UseQueryResult } from '@tanstack/react-query';
import type { CatalogPreset, PresetInspect } from '../../api/types';
import type { PresetEntry } from '../../lib/presets';
import { LiveSystemPrompt } from './LiveSystemPrompt';
import { PresetHeader } from './PresetHeader';
import { ModelLinks, PresetVersions, RawConfig } from './PresetParts';

/**
 * A preset Vibonomics does not keep up to date, read only: what OpenRouter holds for it. Either it was made by hand,
 * and can be adopted, or Vibonomics made it and the owner stopped managing it, and it can be managed again.
 */
export function PresetInspector({
  entry,
  preset,
  inspect,
  onAdopt,
  onManageAgain,
}: {
  entry: PresetEntry;
  preset: CatalogPreset;
  inspect: UseQueryResult<PresetInspect>;
  onAdopt: () => void;
  /** For a preset Vibonomics made and no longer keeps; the hand-made ones are adopted instead. */
  onManageAgain: (() => void) | null;
}) {
  const live = inspect.data;
  const models = live?.models ?? preset.models;
  const providers = live?.providers ?? preset.providers;
  const madeHere = preset.managed;
  return (
    <Stack gap="lg">
      <PresetHeader entry={entry} mark={madeHere} presetId={live?.preset_id} workspace={live?.workspace} />
      {madeHere && (
        <Alert color="blue" variant="light" py="xs">
          Vibonomics made this preset but is not keeping it up to date. It leaves the preset alone until it is managed
          again.
        </Alert>
      )}
      <LiveSystemPrompt present={live?.has_system_prompt ?? preset.has_system_prompt} prompt={live?.system_prompt} />
      <Card withBorder padding="md">
        <Stack gap="md">
          <Group justify="space-between" align="flex-start">
            <Title order={5}>On OpenRouter now</Title>
            <Group gap="xs">
              <Button variant="default" loading={inspect.isFetching} onClick={() => void inspect.refetch()}>
                Refresh
              </Button>
              {madeHere && onManageAgain !== null && <Button onClick={onManageAgain}>Manage again</Button>}
              {!madeHere && <Button onClick={onAdopt}>Adopt…</Button>}
            </Group>
          </Group>
          {inspect.isError && (
            <Alert color="yellow" variant="light" py="xs">
              Could not read this preset from OpenRouter: {inspect.error.message} What is shown is from the last sync.
            </Alert>
          )}
          <Stack gap={2}>
            <Text fw={600} size="sm">
              Models
            </Text>
            <Text size="sm">{models.length > 0 ? <ModelLinks ids={models} /> : 'None set'}</Text>
          </Stack>
          <Stack gap={2}>
            <Text fw={600} size="sm">
              Providers
            </Text>
            <Text size="sm">{providers.length > 0 ? providers.join(', ') : 'Not restricted to any'}</Text>
          </Stack>
          {live !== undefined && <PresetVersions versions={live.versions} current={live.version} />}
          <RawConfig config={live?.config ?? preset.config} />
        </Stack>
      </Card>
    </Stack>
  );
}
