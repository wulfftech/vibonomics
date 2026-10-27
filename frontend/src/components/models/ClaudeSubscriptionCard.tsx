import { Badge, Card, Group, Progress, Stack, Text, Tooltip } from '@mantine/core';
import { resetLabel, type ClaudeUsage } from '../../lib/claudeUsage';
import { money, percent, relativeTime } from '../../lib/format';
import { providerTextColor } from '../../lib/providerColors';
import { tone } from '../../lib/tone';
import { ProviderDot } from '../ProviderDot';
import { UsageWindows } from '../UsageWindows';

export function SubscriptionDetails({ usage }: { usage: ClaudeUsage }) {
  const sessionBits = [
    usage.session?.cost_usd !== null && usage.session?.cost_usd !== undefined && usage.session.cost_usd > 0
      ? `Session ${money(usage.session.cost_usd)}`
      : null,
    usage.session?.context_used_percentage !== null && usage.session?.context_used_percentage !== undefined
      ? `${Math.round(usage.session.context_used_percentage)}% context`
      : null,
    usage.session?.cache_hit_ratio !== null && usage.session?.cache_hit_ratio !== undefined
      ? `${percent(usage.session.cache_hit_ratio)} cached`
      : null,
  ].filter((bit): bit is string => bit !== null);

  return (
    <Stack gap={8}>
      <UsageWindows windows={usage.windows} />
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
      {sessionBits.length > 0 && (
        <Text size="xs" c="dimmed">
          {sessionBits.join(' · ')}
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
            Connected — no plan windows yet. They arrive with Claude Code&apos;s first response in a session.
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
