import { ActionIcon, Anchor, Badge, Card, Group, SimpleGrid, Stack, Text, Tooltip } from '@mantine/core';
import { ExternalLink } from 'lucide-react';
import { Link } from 'react-router';
import { useClaudeSubscription } from '../../api/subscription';
import type { BalanceOut, ProviderOut } from '../../api/types';
import { money, relativeTime } from '../../lib/format';
import { tone } from '../../lib/tone';
import { providerTextColor } from '../../lib/providerColors';
import { ProviderDot } from '../ProviderDot';
import { claudeUsage } from '../../lib/claudeUsage';
import { ClaudeSubscriptionCard } from './ClaudeSubscriptionCard';

function keyLine(balance: BalanceOut): string | null {
  const usage = balance.key_usage ?? null;
  const limit = balance.key_limit ?? null;
  if (usage !== null && limit !== null) {
    return `Key: ${money(usage)} of ${money(limit)} limit`;
  }
  if (usage !== null) {
    return `Key spend: ${money(usage)}`;
  }
  if (balance.key_remaining !== null && balance.key_remaining !== undefined) {
    return `Key: ${money(balance.key_remaining)} left`;
  }
  return null;
}

function accountLine(balance: BalanceOut): string | null {
  if (balance.account_balance !== null && balance.account_balance !== undefined) {
    return `${money(balance.account_balance)} left`;
  }
  if (balance.account_used !== null && balance.account_used !== undefined) {
    return `Spent ${money(balance.account_used)}`;
  }
  return null;
}

function BalanceBody({ provider }: { provider: ProviderOut }) {
  const balance = provider.balance;
  if (balance === null) {
    return (
      <Text size="sm" c="dimmed">
        No balance yet
      </Text>
    );
  }
  switch (balance.status) {
    case 'ok': {
      const account = accountLine(balance);
      const key = keyLine(balance);
      const hint = typeof balance.details?.credits_hint === 'string' ? balance.details.credits_hint : null;
      return (
        <Stack gap={0}>
          {account !== null ? (
            <Text size="sm" fw={600}>
              {account}
            </Text>
          ) : (
            key === null && (
              <Text size="sm" c="dimmed">
                No figures returned
              </Text>
            )
          )}
          {key !== null && (
            <Text
              size={account === null ? 'sm' : 'xs'}
              fw={account === null ? 600 : undefined}
              c={account === null ? undefined : 'dimmed'}
            >
              {key}
            </Text>
          )}
          {account === null && hint !== null && (
            <Text size="xs" c="dimmed">
              {hint}
            </Text>
          )}
          {balance.message && (
            <Text size="xs" style={{ color: tone('yellow') }} lineClamp={1} title={balance.message}>
              {balance.message}
            </Text>
          )}
        </Stack>
      );
    }
    case 'unsupported':
      return (
        <Group gap={4} wrap="nowrap">
          <Text size="sm" c="dimmed">
            Balance not exposed by API
          </Text>
          {provider.billing_url && (
            <Tooltip label="Open the billing page">
              <ActionIcon
                component="a"
                href={provider.billing_url}
                target="_blank"
                rel="noreferrer"
                variant="subtle"
                color="gray"
                size="sm"
                aria-label={`Open the ${provider.name} billing page`}
              >
                <ExternalLink size={14} />
              </ActionIcon>
            </Tooltip>
          )}
        </Group>
      );
    case 'needs_key':
      return (
        <Anchor component={Link} to="/settings/providers" size="sm">
          Add a key
        </Anchor>
      );
    case 'error':
      return (
        <Tooltip label={balance.message ?? 'The balance request failed'} multiline maw={320}>
          <Badge color="red" variant="light" size="sm">
            Error
          </Badge>
        </Tooltip>
      );
    default:
      return (
        <Text size="sm" c="dimmed">
          {balance.status}
        </Text>
      );
  }
}

/** One compact card per enabled provider with what its API says about credit and key spend. */
export function BalancesStrip({ providers }: { providers: ProviderOut[] }) {
  const enabled = providers.filter((provider) => provider.enabled);
  const claude = useClaudeSubscription();
  const usage = claudeUsage(claude.data?.snapshot ?? null, enabled);
  if (enabled.length === 0 && usage === null) {
    return (
      <Text size="sm" c="dimmed">
        No providers are enabled.{' '}
        <Anchor component={Link} to="/settings/providers" size="sm">
          Add one in Settings
        </Anchor>
      </Text>
    );
  }
  return (
    <SimpleGrid cols={{ base: 1, xs: 2, md: 3, lg: 4, xl: 5 }} spacing="sm">
      <ClaudeSubscriptionCard usage={usage} />
      {enabled.map((provider) => (
        <Card key={provider.id} withBorder padding="sm">
          <Stack gap={4}>
            <Group justify="space-between" wrap="nowrap" gap="xs">
              <Group gap={6} wrap="nowrap" miw={0}>
                <ProviderDot type={provider.type} />
                <Text size="sm" fw={500} truncate="end" style={{ color: providerTextColor(provider.type) }}>
                  {provider.name}
                </Text>
              </Group>
              {provider.last_refresh_status === 'error' && (
                <Tooltip label={provider.last_refresh_message ?? 'The last model refresh failed'} multiline maw={320}>
                  <Badge color="red" variant="light" size="xs">
                    Refresh failed
                  </Badge>
                </Tooltip>
              )}
            </Group>
            <BalanceBody provider={provider} />
            {provider.balance?.fetched_at && (
              <Text size="xs" c="dimmed">
                updated {relativeTime(provider.balance.fetched_at)}
              </Text>
            )}
          </Stack>
        </Card>
      ))}
    </SimpleGrid>
  );
}
