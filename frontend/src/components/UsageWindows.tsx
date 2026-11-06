import { Group, Progress, Stack, Text } from '@mantine/core';
import { resetLabel, usageColor } from '../lib/claudeUsage';
import { tone } from '../lib/tone';
import type { UsageWindowView } from '../lib/usageWindows';

/** Progress rows for usage windows (Claude's plan windows, OpenCode Go's rolling/weekly/monthly). */
export function UsageWindows({ windows }: { windows: UsageWindowView[] }) {
  return (
    <Stack gap={8}>
      {windows.map((window) => (
        <Stack key={window.key} gap={2}>
          <Group justify="space-between" wrap="nowrap" gap="xs">
            <Text size="xs" truncate="end">
              {window.label}
            </Text>
            <Text size="xs" fw={600} style={{ color: tone(usageColor(window.used_percentage)) }}>
              {window.used_percentage.toFixed(window.used_percentage < 10 ? 1 : 0)}%
            </Text>
          </Group>
          <Progress value={window.used_percentage} size={4} color={usageColor(window.used_percentage)} />
          {window.limit_reached && (
            <Text size="xs" c="dimmed">
              limit reached
            </Text>
          )}
          {window.resets_at && (
            <Text size="xs" c="dimmed">
              resets {resetLabel(window.resets_at)}
            </Text>
          )}
        </Stack>
      ))}
    </Stack>
  );
}
