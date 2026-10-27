import { Badge, Card, Group, Stack, Text, Tooltip } from '@mantine/core';
import type { ProviderOut } from '../../api/types';
import type { ClaudeUsage } from '../../lib/claudeUsage';
import { relativeTime } from '../../lib/format';
import { providerTextColor } from '../../lib/providerColors';
import { ProviderDot } from '../ProviderDot';
import { BalanceAge, BalanceBody } from './BalanceBody';
import { SubscriptionDetails } from './SubscriptionDetails';

/**
 * One card for the whole Anthropic account: what the Admin API says about money, and — when Claude
 * Code is posting status-line snapshots — the subscription's plan windows. They used to be two cards
 * describing the same account, one of which read as if the subscription were a separate vendor.
 */
export function AnthropicCard({ provider, usage }: { provider: ProviderOut | null; usage: ClaudeUsage | null }) {
  if (provider === null && usage === null) {
    return null;
  }
  const plan = usage?.plan ? usage.plan.charAt(0).toUpperCase() + usage.plan.slice(1) : null;
  return (
    <Card withBorder padding="sm">
      <Stack gap={6}>
        <Group justify="space-between" wrap="nowrap" gap="xs">
          <Group gap={6} wrap="nowrap" miw={0}>
            <ProviderDot type="anthropic" />
            <Text size="sm" fw={500} truncate="end" style={{ color: providerTextColor('anthropic') }}>
              Anthropic (Claude)
            </Text>
          </Group>
          <Group gap={4} wrap="nowrap">
            {usage?.live && (
              <Tooltip label="Claude Code is posting usage snapshots from its status line" withArrow>
                <Badge size="xs" variant="light" color="green">
                  Live
                </Badge>
              </Tooltip>
            )}
            {plan && (
              <Badge size="xs" variant="light" color="orange">
                {plan}
              </Badge>
            )}
            {provider?.last_refresh_status === 'error' && (
              <Tooltip label={provider.last_refresh_message ?? 'The last model refresh failed'} multiline maw={320}>
                <Badge color="red" variant="light" size="xs">
                  Refresh failed
                </Badge>
              </Tooltip>
            )}
          </Group>
        </Group>
        {provider !== null && <BalanceBody provider={provider} />}
        {usage !== null && <SubscriptionDetails usage={usage} />}
        {usage !== null && usage.windows.length === 0 && !usage.extra && !usage.error && (
          <Text size="xs" c="dimmed">
            Connected — no plan windows yet. They arrive with Claude Code&apos;s first response in a session.
          </Text>
        )}
        {usage !== null && (
          <Text size="xs" c="dimmed">
            {usage.live ? 'from Claude Code' : 'from the Anthropic login token'}
            {usage.updatedAt ? ` · ${relativeTime(usage.updatedAt)}` : ''}
            {usage.model ? ` · ${usage.model}` : ''}
          </Text>
        )}
        {provider !== null && <BalanceAge provider={provider} label="API spend updated" />}
      </Stack>
    </Card>
  );
}
