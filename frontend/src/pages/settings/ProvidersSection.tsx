import {
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Code,
  Group,
  Modal,
  Radio,
  ScrollArea,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title,
  Tooltip,
} from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { CircleAlert, ExternalLink, Lock, Plus } from 'lucide-react';
import { useState } from 'react';
import { useEventStream } from '../../api/eventStream';
import {
  useCreateProvider,
  useDeleteProvider,
  useProviders,
  useProviderTypes,
  useRefreshProvider,
  useTestProvider,
  useTestProviderDraft,
  useUpdateProvider,
} from '../../api/providers';
import type { ProviderOut, ProviderTypeOut } from '../../api/types';
import { EmptyState, ErrorState, LoadingState } from '../../components/DataState';
import { ProviderDot } from '../../components/ProviderDot';
import { tone } from '../../lib/tone';
import { SecretInput } from '../../components/SecretInput';
import { DASH, relativeTime } from '../../lib/format';
import { statusColor, statusLabel } from '../../lib/status';

const BALANCE_LABELS: Record<string, { label: string; color: string }> = {
  key: { label: 'key', color: 'green' },
  management: { label: 'management key', color: 'blue' },
  admin: { label: 'admin key', color: 'blue' },
  none: { label: 'none', color: 'gray' },
};

function BalanceSupportBadge({ support, note }: { support: string; note: string }) {
  const entry = BALANCE_LABELS[support] ?? { label: support, color: 'gray' };
  return (
    <Tooltip label={note || 'No balance API'} multiline maw={300} withArrow>
      <Badge size="sm" variant="light" color={entry.color}>
        {entry.label}
      </Badge>
    </Tooltip>
  );
}

function KeyBadge({ provider, envVar }: { provider: ProviderOut; envVar: string | undefined }) {
  if (provider.api_key_locked) {
    return (
      <Tooltip label={`Set by the environment variable ${envVar ?? ''}`.trim()} withArrow>
        <Badge size="sm" variant="light" color="green" leftSection={<Lock size={10} />}>
          Set by env
        </Badge>
      </Tooltip>
    );
  }
  if (provider.api_key_set) {
    return (
      <Badge size="sm" variant="light" color="green">
        Key {provider.api_key_hint ?? ''}
      </Badge>
    );
  }
  return (
    <Badge size="sm" variant="light" color="gray">
      No key
    </Badge>
  );
}

function optionString(value: unknown): string {
  if (value === null || value === undefined) {
    return '';
  }
  return typeof value === 'string' ? value : JSON.stringify(value);
}

/** The add/edit form for one provider. Secrets are never shown; empty means "keep". */
function ProviderForm({
  provider,
  type,
  onClose,
}: {
  provider: ProviderOut | null;
  type: ProviderTypeOut;
  onClose: () => void;
}) {
  const create = useCreateProvider();
  const update = useUpdateProvider();
  const test = useTestProvider();
  const testDraft = useTestProviderDraft();
  const refresh = useRefreshProvider();
  const { markRequested } = useEventStream();

  const [name, setName] = useState(provider?.name ?? type.name);
  const [enabled, setEnabled] = useState(provider?.enabled ?? true);
  const [baseUrl, setBaseUrl] = useState(provider?.base_url ?? '');
  const [apiKey, setApiKey] = useState<string | null>(null);
  const [mgmtKey, setMgmtKey] = useState<string | null>(null);
  const [extraKey, setExtraKey] = useState<string | null>(null);
  const [options, setOptions] = useState<Record<string, string>>(() =>
    Object.fromEntries(type.options.map((o) => [o.key, optionString(provider?.options[o.key])])),
  );
  const [tested, setTested] = useState<{ ok: boolean; message: string } | null>(null);

  const save = provider === null ? create : update;
  const baseUrlMissing = type.custom && baseUrl.trim() === '';
  const optionMissing = type.options.some((o) => o.required && (options[o.key] ?? '').trim() === '');

  const optionPayload = () =>
    Object.fromEntries(
      Object.entries(options)
        .map(([key, value]) => [key, value.trim()] as const)
        .filter(([, value]) => value !== ''),
    );

  const afterSave = (saved: ProviderOut) => {
    markRequested(['models', 'balances']);
    refresh.mutate([saved.id]);
    notifications.show({ message: 'Saved. Refreshing…', color: 'green' });
    onClose();
  };

  const submit = () => {
    if (provider === null) {
      create.mutate(
        {
          type: type.type,
          name: name.trim() || null,
          enabled,
          base_url: baseUrl.trim() || null,
          options: optionPayload(),
          api_key: apiKey,
          mgmt_key: mgmtKey,
          extra_key: extraKey,
        },
        { onSuccess: afterSave },
      );
    } else {
      update.mutate(
        {
          id: provider.id,
          body: {
            name: name.trim() || null,
            enabled,
            base_url: baseUrl.trim(),
            options: optionPayload(),
            api_key: provider.api_key_locked ? null : apiKey,
            mgmt_key: provider.mgmt_key_locked ? null : mgmtKey,
            extra_key: provider.extra_key_locked ? null : extraKey,
          },
        },
        { onSuccess: afterSave },
      );
    }
  };

  return (
    <Stack gap="md">
      <Text size="sm" c="dimmed">
        {type.description}
      </Text>
      <TextInput label="Name" value={name} onChange={(event) => setName(event.currentTarget.value)} />
      <Switch label="Enabled" checked={enabled} onChange={(event) => setEnabled(event.currentTarget.checked)} />
      <TextInput
        label="Base URL"
        required={type.custom}
        description={
          type.custom
            ? 'The OpenAI-compatible endpoint, ending in /v1 or equivalent.'
            : 'Optional override; leave empty for the default.'
        }
        placeholder={type.custom ? 'https://example.com/v1' : type.default_base_url}
        value={baseUrl}
        error={baseUrlMissing && save.isError ? 'Required for custom providers' : undefined}
        onChange={(event) => setBaseUrl(event.currentTarget.value)}
      />
      <SecretInput
        label="API key"
        description={
          <>
            {type.keys_url && (
              <>
                <Anchor href={type.keys_url} target="_blank" rel="noreferrer" size="xs">
                  Get a key
                </Anchor>
                .{' '}
              </>
            )}
            Or set <Code>{type.env_key}</Code>.{type.models_require_key ? ' Models are only listed with a key.' : ''}
          </>
        }
        stored={provider?.api_key_set ?? false}
        locked={provider?.api_key_locked}
        envVar={type.env_key}
        value={apiKey}
        onChange={setApiKey}
      />
      {type.mgmt_key_label && (
        <SecretInput
          label={type.mgmt_key_label}
          description={
            <>
              {type.mgmt_key_help ? `${type.mgmt_key_help} ` : ''}Or set <Code>{type.env_mgmt_key}</Code>.
            </>
          }
          stored={provider?.mgmt_key_set ?? false}
          locked={provider?.mgmt_key_locked}
          envVar={type.env_mgmt_key}
          value={mgmtKey}
          onChange={setMgmtKey}
        />
      )}
      {type.extra_key_label && (
        <SecretInput
          label={type.extra_key_label}
          description={
            <>
              {type.extra_key_help ? `${type.extra_key_help} ` : ''}
              {type.extra_key_env ? (
                <>
                  Or set <Code>{type.extra_key_env}</Code>.
                </>
              ) : null}
            </>
          }
          stored={provider?.extra_key_set ?? false}
          locked={provider?.extra_key_locked}
          envVar={type.extra_key_env ?? undefined}
          value={extraKey}
          onChange={setExtraKey}
        />
      )}
      {type.options.map((option) => (
        <TextInput
          key={option.key}
          label={option.label}
          description={option.description || undefined}
          placeholder={option.placeholder || undefined}
          required={option.required}
          value={options[option.key] ?? ''}
          onChange={(event) => {
            const value = event.currentTarget.value;
            setOptions((old) => ({ ...old, [option.key]: value }));
          }}
        />
      ))}
      {type.balance_note && (
        <Text size="xs" c="dimmed">
          Balance: {type.balance_note}
        </Text>
      )}
      {save.error && (
        <Alert color="red" icon={<CircleAlert size={16} />}>
          {save.error.message}
        </Alert>
      )}
      {tested && (
        <Text size="sm" style={{ color: tested.ok ? tone('green') : tone('red') }}>
          {tested.message}
        </Text>
      )}
      <Group justify="flex-end">
        <Button variant="default" onClick={onClose}>
          Cancel
        </Button>
        <Tooltip label="Tests what is in this form; nothing is saved" withArrow>
          <Button
            variant="default"
            loading={test.isPending || testDraft.isPending}
            disabled={baseUrlMissing || optionMissing}
            onClick={() => {
              setTested(null);
              const done = {
                onSuccess: setTested,
                onError: (error: Error) => setTested({ ok: false, message: error.message }),
              };
              if (provider === null) {
                testDraft.mutate(
                  {
                    type: type.type,
                    name: name.trim() || null,
                    enabled,
                    base_url: baseUrl.trim() || null,
                    options: optionPayload(),
                    api_key: apiKey,
                    mgmt_key: mgmtKey,
                    extra_key: extraKey,
                  },
                  done,
                );
              } else {
                test.mutate(
                  {
                    id: provider.id,
                    body: {
                      api_key: provider.api_key_locked ? undefined : (apiKey ?? undefined),
                      mgmt_key: provider.mgmt_key_locked ? undefined : (mgmtKey ?? undefined),
                      extra_key: provider.extra_key_locked ? undefined : (extraKey ?? undefined),
                      base_url: baseUrl.trim() || undefined,
                      options: optionPayload(),
                    },
                  },
                  done,
                );
              }
            }}
          >
            Test
          </Button>
        </Tooltip>
        <Button loading={save.isPending} disabled={baseUrlMissing || optionMissing} onClick={submit}>
          Save
        </Button>
      </Group>
    </Stack>
  );
}

export function ProvidersSection() {
  const mobile = useMediaQuery('(max-width: 48em)');
  const providers = useProviders();
  const types = useProviderTypes();
  const update = useUpdateProvider();
  const remove = useDeleteProvider();
  const test = useTestProvider();

  const [picking, setPicking] = useState(false);
  const [pickedType, setPickedType] = useState<string | null>(null);
  const [form, setForm] = useState<{ provider: ProviderOut | null; type: string } | null>(null);
  const [pendingDelete, setPendingDelete] = useState<ProviderOut | null>(null);
  const [testingId, setTestingId] = useState<number | null>(null);

  const typeList = types.data ?? [];
  const typeOf = (type: string) => typeList.find((t) => t.type === type);
  const formType = form ? typeOf(form.type) : undefined;
  const rows = providers.data ?? [];

  return (
    <Stack gap="lg">
      <Stack gap={4}>
        <Title order={3}>Providers</Title>
        <Text size="sm" c="dimmed">
          The APIs Vibonomics reads models, prices and balances from. Public catalogues work without a key; a key adds
          live model lists, balances and testing. Keys are encrypted at rest and never sent back to the browser.
        </Text>
      </Stack>

      <Group justify="flex-end">
        <Button
          leftSection={<Plus size={16} />}
          onClick={() => {
            setPickedType(null);
            setPicking(true);
          }}
        >
          Add provider
        </Button>
      </Group>

      {providers.isPending && <LoadingState />}
      {providers.isError && <ErrorState message={providers.error.message} />}
      {providers.data && rows.length === 0 && <EmptyState message="No providers yet." />}

      {rows.length > 0 && (
        <Card withBorder padding="md">
          <Table.ScrollContainer minWidth={900}>
            <Table verticalSpacing="xs">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Name</Table.Th>
                  <Table.Th w={80}>Enabled</Table.Th>
                  <Table.Th w={140}>Key</Table.Th>
                  <Table.Th w={80}>Models</Table.Th>
                  <Table.Th w={140}>Last refresh</Table.Th>
                  <Table.Th w={140}>Balance API</Table.Th>
                  <Table.Th w={210} />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {rows.map((provider) => {
                  const type = typeOf(provider.type);
                  return (
                    <Table.Tr key={provider.id}>
                      <Table.Td>
                        <Group gap={6} wrap="nowrap">
                          <ProviderDot type={provider.type} />
                          <Text size="sm" fw={500}>
                            {provider.name}
                          </Text>
                        </Group>
                        <Text size="xs" c="dimmed">
                          {type?.name ?? provider.type}
                          {provider.base_url ? ` · ${provider.base_url}` : ''}
                        </Text>
                      </Table.Td>
                      <Table.Td>
                        <Switch
                          aria-label={`Enable ${provider.name}`}
                          checked={provider.enabled}
                          disabled={update.isPending && update.variables?.id === provider.id}
                          onChange={(event) =>
                            update.mutate(
                              { id: provider.id, body: { enabled: event.currentTarget.checked } },
                              { onError: (error) => notifications.show({ message: error.message, color: 'red' }) },
                            )
                          }
                        />
                      </Table.Td>
                      <Table.Td>
                        <KeyBadge provider={provider} envVar={type?.env_key} />
                      </Table.Td>
                      <Table.Td>{provider.model_count.toLocaleString()}</Table.Td>
                      <Table.Td>
                        {provider.last_refresh_status ? (
                          <Tooltip
                            label={`${provider.last_refresh_message ?? statusLabel(provider.last_refresh_status)} · ${relativeTime(provider.last_refresh_at)}`}
                            multiline
                            maw={360}
                            withArrow
                          >
                            <Badge size="sm" variant="light" color={statusColor(provider.last_refresh_status)}>
                              {statusLabel(provider.last_refresh_status)}
                            </Badge>
                          </Tooltip>
                        ) : (
                          <Text size="sm" c="dimmed">
                            {DASH}
                          </Text>
                        )}
                      </Table.Td>
                      <Table.Td>
                        <Group gap={4} wrap="nowrap">
                          <BalanceSupportBadge support={provider.balance_support} note={provider.balance_note} />
                          {provider.billing_url && (
                            <Anchor
                              href={provider.billing_url}
                              target="_blank"
                              rel="noreferrer"
                              aria-label={`Open the ${provider.name} billing page`}
                            >
                              <ExternalLink size={12} />
                            </Anchor>
                          )}
                        </Group>
                      </Table.Td>
                      <Table.Td>
                        <Group gap="xs" justify="flex-end" wrap="nowrap">
                          <Button
                            variant="default"
                            size="xs"
                            loading={test.isPending && testingId === provider.id}
                            onClick={() => {
                              setTestingId(provider.id);
                              test.mutate(
                                { id: provider.id },
                                {
                                  onSuccess: (result) =>
                                    notifications.show({ message: result.message, color: result.ok ? 'green' : 'red' }),
                                  onError: (error) => notifications.show({ message: error.message, color: 'red' }),
                                },
                              );
                            }}
                          >
                            Test
                          </Button>
                          <Button
                            variant="light"
                            size="xs"
                            disabled={type === undefined}
                            onClick={() => setForm({ provider, type: provider.type })}
                          >
                            Edit
                          </Button>
                          <Button variant="light" color="red" size="xs" onClick={() => setPendingDelete(provider)}>
                            Delete
                          </Button>
                        </Group>
                      </Table.Td>
                    </Table.Tr>
                  );
                })}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </Card>
      )}

      <Modal opened={picking} onClose={() => setPicking(false)} title="Add a provider" size="lg" fullScreen={mobile}>
        <Stack gap="md">
          {types.isPending && <LoadingState />}
          {types.isError && <ErrorState message={types.error.message} />}
          <ScrollArea.Autosize mah="60vh" offsetScrollbars>
            <Radio.Group value={pickedType ?? ''} onChange={setPickedType} aria-label="Provider type">
              <Stack gap="sm">
                {typeList.map((type) => (
                  <Radio
                    key={type.type}
                    value={type.type}
                    label={
                      <Group gap={6}>
                        <Text size="sm" fw={500}>
                          {type.name}
                        </Text>
                        <BalanceSupportBadge support={type.balance} note={type.balance_note} />
                      </Group>
                    }
                    description={type.description}
                  />
                ))}
              </Stack>
            </Radio.Group>
          </ScrollArea.Autosize>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setPicking(false)}>
              Cancel
            </Button>
            <Button
              disabled={pickedType === null}
              onClick={() => {
                if (pickedType !== null) {
                  setPicking(false);
                  setForm({ provider: null, type: pickedType });
                }
              }}
            >
              Continue
            </Button>
          </Group>
        </Stack>
      </Modal>

      <Modal
        opened={form !== null && formType !== undefined}
        onClose={() => setForm(null)}
        title={form?.provider ? `Edit ${form.provider.name}` : `Add ${formType?.name ?? 'provider'}`}
        size="lg"
        fullScreen={mobile}
      >
        {form !== null && formType !== undefined && (
          <ProviderForm
            key={form.provider?.id ?? form.type}
            provider={form.provider}
            type={formType}
            onClose={() => setForm(null)}
          />
        )}
      </Modal>

      <Modal opened={pendingDelete !== null} onClose={() => setPendingDelete(null)} title="Delete provider">
        <Stack gap="md">
          <Text size="sm">
            Delete {pendingDelete?.name ?? 'this provider'}? Its models, prices and stored keys are removed from
            Vibonomics. Test results are kept.
          </Text>
          {remove.error && (
            <Text size="sm" style={{ color: tone('red') }}>
              {remove.error.message}
            </Text>
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setPendingDelete(null)}>
              Cancel
            </Button>
            <Button
              color="red"
              loading={remove.isPending}
              onClick={() => {
                if (pendingDelete !== null) {
                  remove.mutate(pendingDelete.id, {
                    onSuccess: () => {
                      notifications.show({ message: 'Provider deleted', color: 'green' });
                      setPendingDelete(null);
                    },
                  });
                }
              }}
            >
              Delete
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
