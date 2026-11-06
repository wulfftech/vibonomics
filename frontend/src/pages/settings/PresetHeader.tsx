import { Badge, Code, Group, Stack, Title } from '@mantine/core';
import { CopyIconButton } from '../../components/CopyIconButton';
import { VibonomicsMark } from '../../components/VibonomicsMark';
import type { PresetEntry } from '../../lib/presets';
import { openRouterPresetUrl } from '../../lib/presets';
import { PresetPageLink } from './PresetParts';

/**
 * The top of a preset's page: its name, the `@preset/` address to use as a model (with a copy button), the Vibonomics
 * mark if it is managed, the live version and status, and a link to the preset on OpenRouter once it has one.
 * The catalogue holds the id but not the workspace; `workspace` (and a fresher `presetId`) come from a plan or an
 * inspection, which name both.
 */
export function PresetHeader({
  entry,
  mark,
  presetId,
  workspace,
}: {
  entry: PresetEntry;
  mark: boolean;
  presetId?: string | null;
  workspace?: string | null;
}) {
  const url = openRouterPresetUrl(presetId ?? entry.catalog?.preset_id, workspace);
  return (
    <Stack gap={6}>
      <Group gap="xs" wrap="nowrap">
        <Title order={4} style={{ overflowWrap: 'anywhere' }}>
          {entry.name}
        </Title>
        {mark && <VibonomicsMark size={18} />}
      </Group>
      <Group gap="xs" align="center">
        <Code>@preset/{entry.slug}</Code>
        <CopyIconButton value={`@preset/${entry.slug}`} label="Copy the preset address" />
        {entry.notCreated ? (
          <Badge variant="light" color="yellow">
            not created yet
          </Badge>
        ) : (
          <>
            {entry.version !== null && <Badge variant="light">v{entry.version}</Badge>}
            <Badge variant="light" color={entry.archived ? 'gray' : 'teal'}>
              {entry.archived ? 'archived' : 'active'}
            </Badge>
          </>
        )}
        <PresetPageLink url={url} />
      </Group>
    </Stack>
  );
}
