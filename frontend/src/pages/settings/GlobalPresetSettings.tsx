import { Card, Stack, Text, Title } from '@mantine/core';
import type { ReactNode } from 'react';
import type { FullSettings } from '../../api/types';
import { BLOCKLIST_HELP } from '../../lib/presets';
import { BlocklistField } from './BlocklistField';

type Presets = FullSettings['presets'];

/** What every preset shares. For now that is the blocklists; each preset adds its own on top. */
export function GlobalPresetSettings({
  presets,
  onChange,
  saveBar,
}: {
  presets: Presets;
  onChange: (patch: Partial<Pick<Presets, 'blocked_models' | 'blocked_providers'>>) => void;
  saveBar: ReactNode;
}) {
  return (
    <Stack gap="lg">
      <Stack gap={4}>
        <Title order={4}>Global settings</Title>
        <Text size="sm" c="dimmed">
          These apply to every preset. A preset can block more of its own.
        </Text>
      </Stack>
      <Card withBorder padding="md">
        <Stack gap="md">
          <Text size="sm">{BLOCKLIST_HELP}</Text>
          <BlocklistField
            label="Blocked models"
            description="Matched against the model id, such as openai/gpt-5 or *:free"
            placeholder="openai/*"
            value={presets.blocked_models}
            onChange={(blocked_models) => onChange({ blocked_models })}
          />
          <BlocklistField
            label="Blocked providers"
            description="Matched against a provider's tag and name, such as deepinfra"
            placeholder="deepinfra"
            value={presets.blocked_providers}
            onChange={(blocked_providers) => onChange({ blocked_providers })}
          />
        </Stack>
      </Card>
      {saveBar}
    </Stack>
  );
}
