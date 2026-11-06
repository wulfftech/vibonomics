import { Group, Progress, Stack, Text, Tooltip } from '@mantine/core';
import { money, percent } from '../../lib/format';
import { resetLabel, type ClaudeUsage } from '../../lib/claudeUsage';
import { tone } from '../../lib/tone';
import { UsageWindows } from '../UsageWindows';

/** The subscription half of the Claude card: plan windows, usage credits and the live session figures. */
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
                ? `${money(usage.extra.used ?? 0, usage.extra.currency)} of ${money(usage.extra.limit, usage.extra.currency)}`
                : `${money(usage.extra.used ?? 0, usage.extra.currency)} spent`}
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
