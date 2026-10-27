import { Badge, Card, Group, Progress, Stack, Text, Tooltip } from '@mantine/core';
import { resetLabel, usageColor, type ClaudeUsage } from '../../lib/claudeUsage';
import { money, relativeTime } from '../../lib/format';
import { providerTextColor } from '../../lib/providerColors';
import { tone } from '../../lib/tone';
import { ProviderDot } from '../ProviderDot';

export function SubscriptionDetails({ usage }: { usage: ClaudeUsage }) {
  return (
    <Stack gap={8}>
      {usage.windows.map((window) => (
        <Stack key={window.key} gap={2}>
          <Group justify="space-between" wrap="nowrap" gap="xs">
            <Text size="xs" truncate="end">
              {window.label}
            </Text>
            <Text size="xs" fw={600} c={usageColor(window.used_percentage)}>
              {window.used_percentage.toFixed(window.used_percentage < 10 ? 1 : 0)}%
            </Text>
          </Group>
          <Progress value={Math.min(100, window.used_percentage)} size={4} color={usageColor(window.used_percentage)} />
          {window.resets_at && (
            <Text size="xs" c="dimmed">
              resets {resetLabel(window.resets_at)}
            </Text>
          )}
        </Stack>
      ))}
      {usage.extra && (
        <Stack gap={2}>
          <Group justify="space-between" wrap="nowrap" gap="xs">
            <Text size="xs">Usage credits this month</Text>
            <Text size="xs" fw={600}>
              {usage.extra.limit !== null && usage.extra.limit !== undefined
                ? `${money(usage.extra.used ?? 0)} of ${money(usage.extra.limit)}`
                : `${money(usage.extra.used ?? 0)} spent`}
            </Text>
          </Group>
          {usage.extra.utilization !== null && usage.extra.utilization !== undefined && (
            <Progress
              value={Math.min(
                100,
                usage.extra.utilization <= 1 ? usage.extra.utilization * 100 : usage.extra.utilization,
              )}
              size={4}
              color="grape"
            />
          )}
        </Stack>
      )}
      {usage.spendLimit && (
        <Text size="xs" c="dimmed">
          Gateway spend: {money(usage.spendLimit.used_usd)} of {money(usage.spendLimit.limit_usd)}
          {usage.spendLimit.resets_at ? ` · resets ${resetLabel(usage.spendLimit.resets_at)}` : ''}
        </Text>
      )}
      {usage.error && (
        <Tooltip label={usage.error} multiline maw={340} withArrow>
          <Text size="xs" style={{ color: tone('yellow') }} lineClamp={2}>
            Login token problem: {usage.error}
          </Text>
        </Tooltip>
      )}
    </Stack>
  );
}

/** One compact card for the Claude subscription: 5-hour/weekly windows and optional credit spend. */
export function ClaudeSubscriptionCard({ usage }: { usage: ClaudeUsage | null }) {
  if (usage === null) {
    return null;
  }
  const plan = usage.plan ? usage.plan.charAt(0).toUpperCase() + usage.plan.slice(1) : null;
  return (
    <Card withBorder padding="sm">
      <Stack gap={6}>
        <Group justify="space-between" wrap="nowrap" gap="xs">
          <Group gap={6} wrap="nowrap" miw={0}>
            <ProviderDot type="anthropic" />
            <Text size="sm" fw={500} truncate="end" style={{ color: providerTextColor('anthropic') }}>
              Claude subscription
            </Text>
          </Group>
          <Group gap={4} wrap="nowrap">
            {usage.live && (
              <Badge size="xs" variant="light" color="green">
                Live
              </Badge>
            )}
            {plan && (
              <Badge size="xs" variant="light" color="orange">
                {plan}
              </Badge>
            )}
          </Group>
        </Group>
        <SubscriptionDetails usage={usage} />
        {usage.windows.length === 0 && !usage.extra && !usage.error && (
          <Text size="xs" c="dimmed">
            Connected — waiting for Claude Code to send usage.
          </Text>
        )}
        <Text size="xs" c="dimmed">
          {usage.live ? 'from Claude Code' : 'from the Anthropic login token'}
          {usage.updatedAt ? ` · ${relativeTime(usage.updatedAt)}` : ''}
          {usage.model ? ` · ${usage.model}` : ''}
        </Text>
      </Stack>
    </Card>
  );
}
