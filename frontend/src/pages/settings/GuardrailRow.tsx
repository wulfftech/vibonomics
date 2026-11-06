import { Group, SegmentedControl, Stack, Text } from '@mantine/core';
import type { ReactNode } from 'react';
import type { GuardrailMode } from '../../lib/presets';

const MODES = [
  { value: 'leave', label: 'Leave' },
  { value: 'set', label: 'Set' },
  { value: 'remove', label: 'Remove' },
];

/**
 * One guardrail: its name and the choice between leaving it as it is on OpenRouter, setting it, and removing it; the
 * value input, which the caller turns off unless it is Set; what is on OpenRouter now; and the notes that matter.
 */
export function GuardrailRow({
  label,
  mode,
  onMode,
  live,
  notes = [],
  children,
}: {
  label: string;
  mode: GuardrailMode;
  onMode: (mode: GuardrailMode) => void;
  /** What the live preset holds for it, in a few words. */
  live: string;
  notes?: string[];
  children: ReactNode;
}) {
  return (
    <Stack gap={6}>
      <Group justify="space-between" align="center" gap="sm">
        <Text fw={600} size="sm">
          {label}
        </Text>
        <SegmentedControl
          size="xs"
          aria-label={`${label} setting`}
          data={MODES}
          value={mode}
          onChange={(next) => onMode(next as GuardrailMode)}
        />
      </Group>
      {children}
      <Text size="xs" c="dimmed">
        On OpenRouter now: {live}
      </Text>
      {notes.map((note) => (
        <Text key={note} size="xs" c="dimmed">
          {note}
        </Text>
      ))}
    </Stack>
  );
}
