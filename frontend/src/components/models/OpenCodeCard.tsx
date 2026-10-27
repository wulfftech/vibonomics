import { ActionIcon, Badge, Card, Group, Stack, Text, Tooltip } from '@mantine/core';
import { ExternalLink } from 'lucide-react';
import type { ProviderOut } from '../../api/types';
import { providerColor, providerTextColor } from '../../lib/providerColors';
import { ProviderDot } from '../ProviderDot';
import { BalanceAge, BalanceBody } from './BalanceBody';

/** The Freshest balance timestamp among the providers on one card. */
function newestBalance(providers: ProviderOut[]): ProviderOut | null {
  return providers.reduce<ProviderOut | null>((best, provider) => {
    const stamp = provider.balance?.fetched_at;
    if (!stamp) {
      return best;
    }
    const bestStamp = best?.balance?.fetched_at;
    if (!bestStamp) {
      return provider;
    }
    return new Date(stamp) > new Date(bestStamp) ? provider : best;
  }, null);
}

/**
 * One card for both OpenCode products. They share a vendor and an account but bill differently: Go is
 * the monthly subscription whose usage endpoint reports plan windows, Console is pay-as-you-go credit
 * that no API exposes. Two cards showed the same vendor twice, and one of them could never say anything.
 */
export function OpenCodeCard({
  consoleProvider,
  goProvider,
}: {
  consoleProvider: ProviderOut | null;
  goProvider: ProviderOut | null;
}) {
  if (consoleProvider === null && goProvider === null) {
    return null;
  }
  const members = [goProvider, consoleProvider].filter((provider): provider is ProviderOut => provider !== null);
  const failed = members.filter((provider) => provider.last_refresh_status === 'error');
  const newest = newestBalance(members);
  const consoleCredits = consoleProvider?.billing_url ?? goProvider?.billing_url ?? null;

  return (
    <Card withBorder padding="sm">
      <Stack gap={6}>
        <Group justify="space-between" wrap="nowrap" gap="xs">
          <Group gap={6} wrap="nowrap" miw={0}>
            <ProviderDot type="opencode" />
            <Text size="sm" fw={500} truncate="end" style={{ color: providerTextColor('opencode') }}>
              OpenCode
            </Text>
          </Group>
          <Group gap={4} wrap="nowrap">
            {goProvider !== null && (
              <Tooltip label="This workspace has the Go subscription, so plan windows are reported" withArrow>
                <Badge size="xs" variant="light" color={providerColor('opencode-go')}>
                  Go
                </Badge>
              </Tooltip>
            )}
            {failed.length > 0 && (
              <Tooltip label={failed[0].last_refresh_message ?? 'The last model refresh failed'} multiline maw={320}>
                <Badge color="red" variant="light" size="xs">
                  Refresh failed
                </Badge>
              </Tooltip>
            )}
          </Group>
        </Group>

        {goProvider !== null && <BalanceBody provider={goProvider} />}

        {consoleProvider !== null &&
          (consoleProvider.balance?.status === 'unsupported' ? (
            <Group gap={4} wrap="nowrap">
              <Text size="xs" c="dimmed">
                Console credit is only visible in the OpenCode console
              </Text>
              {consoleCredits && (
                <Tooltip label="Open the OpenCode console">
                  <ActionIcon
                    component="a"
                    href={consoleCredits}
                    target="_blank"
                    rel="noreferrer"
                    variant="subtle"
                    color="gray"
                    size="sm"
                    aria-label="Open the OpenCode console"
                  >
                    <ExternalLink size={14} />
                  </ActionIcon>
                </Tooltip>
              )}
            </Group>
          ) : (
            <BalanceBody provider={consoleProvider} />
          ))}

        {newest !== null && <BalanceAge provider={newest} />}
      </Stack>
    </Card>
  );
}
