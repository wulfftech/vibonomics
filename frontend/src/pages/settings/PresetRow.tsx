import { Badge, Group, Stack, Text, UnstyledButton } from '@mantine/core';
import type { ReactNode } from 'react';
import { VibonomicsMark } from '../../components/VibonomicsMark';
import type { PresetEntry } from '../../lib/presets';

/** A row of the preset list. It is a real button: Tab reaches it, Enter and Space choose it. */
export function RowButton({
  selected,
  onClick,
  title,
  detail,
  mark = false,
  aside,
}: {
  selected: boolean;
  onClick: () => void;
  title: string;
  detail?: string;
  mark?: boolean;
  aside?: ReactNode;
}) {
  return (
    <UnstyledButton
      type="button"
      onClick={onClick}
      aria-current={selected ? 'true' : undefined}
      w="100%"
      px="xs"
      py={6}
      style={{
        flexShrink: 0,
        borderRadius: 'var(--mantine-radius-md)',
        border: `1px solid ${selected ? 'var(--mantine-primary-color-filled)' : 'var(--mantine-color-default-border)'}`,
        background: selected ? 'var(--mantine-primary-color-light)' : undefined,
      }}
    >
      <Group gap="xs" wrap="nowrap" align="flex-start" justify="space-between">
        <Stack gap={0} style={{ minWidth: 0, flex: 1 }}>
          <Group gap={6} wrap="nowrap">
            <Text size="sm" fw={500} truncate="end">
              {title}
            </Text>
            {mark && <VibonomicsMark />}
          </Group>
          {detail !== undefined && (
            <Text size="xs" c="dimmed" truncate="end">
              {detail}
            </Text>
          )}
        </Stack>
        {aside}
      </Group>
    </UnstyledButton>
  );
}

/** One preset of the list: its name and slug, version, and what is the matter with it, if anything. */
export function PresetEntryRow({
  entry,
  selected,
  unsaved,
  onClick,
}: {
  entry: PresetEntry;
  selected: boolean;
  unsaved: boolean;
  onClick: () => void;
}) {
  return (
    <RowButton
      selected={selected}
      onClick={onClick}
      title={entry.name}
      detail={entry.name === entry.slug ? undefined : entry.slug}
      mark={entry.managed}
      aside={
        <Stack gap={2} align="flex-end" style={{ flexShrink: 0 }}>
          {entry.version !== null && (
            <Text size="xs" c="dimmed">
              v{entry.version}
            </Text>
          )}
          {entry.archived && (
            <Badge size="xs" variant="light" color="gray">
              archived
            </Badge>
          )}
          {entry.notCreated && (
            <Badge size="xs" variant="light" color="yellow">
              not created yet
            </Badge>
          )}
          {unsaved && !entry.notCreated && (
            <Badge size="xs" variant="light" color="blue">
              unsaved
            </Badge>
          )}
        </Stack>
      }
    />
  );
}
