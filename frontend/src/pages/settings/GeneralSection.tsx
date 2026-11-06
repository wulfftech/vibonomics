import { Badge, Card, Code, Group, Stack, Text, TextInput, Title } from '@mantine/core';
import type { ReactNode } from 'react';
import { useSystemStatus } from '../../api/system';
import { ErrorState, LoadingState } from '../../components/DataState';
import { DASH } from '../../lib/format';
import { SaveBar } from './SaveBar';
import { useSettingsDraft } from './useSettingsDraft';

function InfoRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Group justify="space-between" wrap="nowrap" align="flex-start">
      <Text size="sm" c="dimmed">
        {label}
      </Text>
      <Stack gap={2} align="flex-end">
        {children}
      </Stack>
    </Group>
  );
}

export function GeneralSection() {
  const { query, value, update, dirty, submit, saving, reset } = useSettingsDraft(['general'], {
    general: ['instance_name'],
  });
  const system = useSystemStatus();

  if (query.isPending) {
    return <LoadingState />;
  }
  if (query.isError || value === null || query.data === undefined) {
    return <ErrorState message={query.error?.message} />;
  }

  return (
    <Stack gap="lg">
      <Stack gap={4}>
        <Title order={3}>General</Title>
        <Text size="sm" c="dimmed">
          This instance&apos;s name and how it&apos;s secured.
        </Text>
      </Stack>

      <Card withBorder padding="md">
        <Stack gap="md">
          <TextInput
            label="Instance name"
            description="Shown in the header and the browser tab"
            maw={360}
            value={value.general.instance_name}
            onChange={(event) => {
              const name = event.currentTarget.value;
              update((old) => ({ ...old, general: { ...old.general, instance_name: name } }));
            }}
          />
        </Stack>
      </Card>

      <Card withBorder padding="md">
        <Stack gap="sm">
          <Title order={4}>About</Title>
          <InfoRow label="Version">
            <Text size="sm">{system.data?.version ?? DASH}</Text>
          </InfoRow>
          <InfoRow label="URL base">
            <Text size="sm">{system.data?.url_base ? <Code>{system.data.url_base}</Code> : '/'}</Text>
          </InfoRow>
          <InfoRow label="Password protection">
            <Badge variant="light" color={query.data.auth_enabled ? 'green' : 'gray'}>
              {query.data.auth_enabled ? 'On' : 'Off'}
            </Badge>
            <Text size="xs" c="dimmed">
              Set <Code>VIBONOMICS_PASSWORD</Code> to require a password, or use your reverse proxy&apos;s auth.
            </Text>
          </InfoRow>
          <InfoRow label="Encryption key">
            <Text size="sm">{query.data.secret_key_from_env ? 'From the environment' : 'Generated key file'}</Text>
            <Text size="xs" c="dimmed">
              {query.data.secret_key_from_env ? (
                <>
                  Read from <Code>VIBONOMICS_SECRET_KEY</Code>.
                </>
              ) : (
                <>
                  Stored as <Code>secret.key</Code> in the config directory; back it up with the database, or set{' '}
                  <Code>VIBONOMICS_SECRET_KEY</Code>.
                </>
              )}
            </Text>
          </InfoRow>
        </Stack>
      </Card>

      <SaveBar
        dirty={dirty}
        canSave={value.general.instance_name.trim() !== ''}
        saving={saving}
        onReset={reset}
        onSave={() => submit()}
      />
    </Stack>
  );
}
