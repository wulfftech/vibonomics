import { ActionIcon, Anchor, Badge, Group, Stack, Text, Tooltip } from '@mantine/core';
import { ExternalLink, RefreshCw } from 'lucide-react';
import { Link } from 'react-router';
import { useRefreshProvider } from '../../api/providers';
import type { BalanceOut, ProviderOut } from '../../api/types';
import { money, relativeTime } from '../../lib/format';
import { tone } from '../../lib/tone';
import { balanceWindows } from '../../lib/usageWindows';
import { UsageWindows } from '../UsageWindows';

function keyLine(balance: BalanceOut): string | null {
  const currency = balance.currency;
  const usage = balance.key_usage ?? null;
  const limit = balance.key_limit ?? null;
  if (usage !== null && limit !== null) {
    return `Key: ${money(usage, currency)} of ${money(limit, currency)} limit`;
  }
  if (usage !== null) {
    return `Key spend: ${money(usage, currency)}`;
  }
  if (balance.key_remaining !== null && balance.key_remaining !== undefined) {
    return `Key: ${money(balance.key_remaining, currency)} left`;
  }
  return null;
}

function accountLine(balance: BalanceOut): string | null {
  if (balance.account_balance !== null && balance.account_balance !== undefined) {
    return `${money(balance.account_balance, balance.currency)} left`;
  }
  if (balance.account_used !== null && balance.account_used !== undefined) {
    // Providers that report spend name the window (Anthropic: "this month"); others just say "Spent".
    const period = typeof balance.details?.spend_period === 'string' ? ` ${balance.details.spend_period}` : '';
    return `Spent ${money(balance.account_used, balance.currency)}${period}`;
  }
  return null;
}

/** What a provider's balance API says: credit, key spend and any usage windows it publishes. */
export function BalanceBody({ provider }: { provider: ProviderOut }) {
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
      const windows = balanceWindows(balance);
      const hint = typeof balance.details?.credits_hint === 'string' ? balance.details.credits_hint : null;
      const trailing = typeof balance.details?.last_30_days === 'number' ? balance.details.last_30_days : null;
      return (
        <Stack gap={windows.length > 0 ? 6 : 0}>
          {account !== null ? (
            <Text size="sm" fw={600}>
              {account}
            </Text>
          ) : (
            key === null &&
            windows.length === 0 && (
              <Text size="sm" c="dimmed">
                No figures returned
              </Text>
            )
          )}
          {trailing !== null && trailing !== balance.account_used && (
            <Text size="xs" c="dimmed">
              Last 30 days {money(trailing, balance.currency)}
            </Text>
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
          {windows.length > 0 && <UsageWindows windows={windows} />}
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

/** "updated 2 h ago", or nothing when the provider has never reported a balance. */
export function BalanceAge({ provider, label = 'updated' }: { provider: ProviderOut; label?: string }) {
  const fetchedAt = provider.balance?.fetched_at;
  if (!fetchedAt) {
    return null;
  }
  return (
    <Text size="xs" c="dimmed">
      {label} {relativeTime(fetchedAt)}
    </Text>
  );
}

/**
 * The card's footer: when the figures were last fetched, and a button that refreshes just this card's
 * providers. A card that stands for two providers (OpenCode) passes both, so one click updates all of it.
 */
export function BalanceFooter({ providers, label = 'updated' }: { providers: ProviderOut[]; label?: string }) {
  const refresh = useRefreshProvider();
  const newest = providers.reduce<ProviderOut | null>((best, provider) => {
    const stamp = provider.balance?.fetched_at;
    if (!stamp) {
      return best;
    }
    const bestStamp = best?.balance?.fetched_at;
    return !bestStamp || new Date(stamp) > new Date(bestStamp) ? provider : best;
  }, null);
  const names = providers.map((provider) => provider.name).join(' and ');
  return (
    <Group justify="space-between" wrap="nowrap" gap="xs">
      <Text size="xs" c="dimmed" truncate="end">
        {newest?.balance?.fetched_at ? `${label} ${relativeTime(newest.balance.fetched_at)}` : 'never refreshed'}
      </Text>
      <Tooltip label={`Refresh ${names}`} withArrow>
        <ActionIcon
          variant="subtle"
          color="gray"
          size="sm"
          aria-label={`Refresh ${names}`}
          loading={refresh.isPending}
          disabled={providers.length === 0}
          onClick={() => refresh.mutate(providers.map((provider) => provider.id))}
        >
          <RefreshCw size={14} />
        </ActionIcon>
      </Tooltip>
    </Group>
  );
}
