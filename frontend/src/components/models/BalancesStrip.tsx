import { Anchor, Badge, Card, Group, SimpleGrid, Stack, Text, Tooltip } from '@mantine/core';
import type { ReactNode } from 'react';
import { Link } from 'react-router';
import { useClaudeSubscription } from '../../api/subscription';
import type { ProviderOut } from '../../api/types';
import { claudeUsage } from '../../lib/claudeUsage';
import { providerTextColor } from '../../lib/providerColors';
import { ProviderDot } from '../ProviderDot';
import { AnthropicCard } from './AnthropicCard';
import { BalanceFooter, BalanceBody } from './BalanceBody';
import { OpenCodeCard } from './OpenCodeCard';

/** A provider that stands alone on the strip: one vendor, one card. */
function ProviderCard({ provider }: { provider: ProviderOut }) {
  return (
    <Card withBorder padding="sm">
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
        <BalanceFooter providers={[provider]} />
      </Stack>
    </Card>
  );
}

/**
 * One compact card per vendor with what its APIs say about credit, spend and plan usage.
 *
 * Anthropic and OpenCode each get a single card even when they arrive as two provider rows, because
 * the operator thinks of them as one account: Anthropic's Admin API spend belongs beside the Claude
 * subscription's plan windows, and Console credit belongs beside the Go plan's windows.
 */
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

  const cards: ReactNode[] = [];
  const merged = new Set<string>();
  // The first Console row and the first Go row make the one OpenCode card; any further row stands alone, like a
  // second Anthropic row, so its balance and its refresh error still show.
  const consoleProvider = enabled.find((entry) => entry.type === 'opencode') ?? null;
  const goProvider = enabled.find((entry) => entry.type === 'opencode-go') ?? null;
  if (usage !== null && !enabled.some((provider) => provider.type === 'anthropic')) {
    cards.push(<AnthropicCard key="claude" provider={null} usage={usage} />);
  }
  for (const provider of enabled) {
    if (provider.type === 'anthropic' && !merged.has('anthropic')) {
      merged.add('anthropic');
      cards.push(<AnthropicCard key="anthropic" provider={provider} usage={usage} />);
      continue;
    }
    if (provider === consoleProvider || provider === goProvider) {
      // The two rows belong to the one card; the second must not fall through as its own.
      if (!merged.has('opencode')) {
        merged.add('opencode');
        cards.push(<OpenCodeCard key="opencode" consoleProvider={consoleProvider} goProvider={goProvider} />);
      }
      continue;
    }
    cards.push(<ProviderCard key={provider.id} provider={provider} />);
  }
  return (
    <SimpleGrid cols={{ base: 1, xs: 2, md: 3, lg: 4, xl: 5 }} spacing="sm">
      {cards}
    </SimpleGrid>
  );
}
