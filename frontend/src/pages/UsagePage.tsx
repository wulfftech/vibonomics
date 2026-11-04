import {
  Alert,
  Anchor,
  Button,
  Card,
  Group,
  Paper,
  Select,
  SegmentedControl,
  SimpleGrid,
  Stack,
  Text,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useState, type ReactNode } from 'react';
import { Link } from 'react-router';
import { useSpend } from '../api/usage';
import { useCanChange, useTriggerRefresh } from '../api/system';
import type { SpendOut } from '../api/types';
import { ErrorState, LoadingState } from '../components/DataState';
import { BreakdownTable, type BreakdownRow } from '../components/usage/BreakdownTable';
import { DailyCharts } from '../components/usage/DailyCharts';
import { dateOnly, integer, money, percent, relativeTime } from '../lib/format';
import { bigCount, dayLabel } from '../lib/usage';

const RANGES = [
  { value: '7', label: '7 days' },
  { value: '30', label: '30 days' },
  { value: '90', label: '90 days' },
  { value: '3650', label: 'All' },
];

function Tile({ label, value, sub }: { label: string; value: string; sub?: ReactNode }) {
  return (
    <Paper withBorder p="md" role="group" aria-label={label}>
      <Text size="sm" c="dimmed">
        {label}
      </Text>
      <Text fz={26} fw={600} lh={1.25}>
        {value}
      </Text>
      {sub && (
        <Text size="xs" c="dimmed">
          {sub}
        </Text>
      )}
    </Paper>
  );
}

function latest(values: (string | null)[]): string | null {
  const present = values.filter((v): v is string => v !== null);
  return present.length === 0 ? null : present.reduce((a, b) => (a > b ? a : b));
}

function Empty({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Card withBorder padding="lg">
      <Stack gap="xs" align="flex-start">
        <Title order={4}>{title}</Title>
        <Text size="sm" c="dimmed" maw={620}>
          {children}
        </Text>
      </Stack>
    </Card>
  );
}

/** What OpenRouter has charged this account: how much, in what, and what a token cost. */
export function UsagePage() {
  const [days, setDays] = useState(30);
  const [providerId, setProviderId] = useState<number | null>(null);
  const [key, setKey] = useState<string | null>(null);
  const canChange = useCanChange();
  const spend = useSpend({ days, providerId, key });
  const refresh = useTriggerRefresh();

  if (spend.isPending) {
    return <LoadingState />;
  }
  if (spend.isError) {
    return <ErrorState message={spend.error.message} />;
  }
  const data: SpendOut = spend.data;
  const accounts = data.accounts;

  const header = (
    <Stack gap={4}>
      <Title order={2}>Usage</Title>
      <Text size="sm" c="dimmed">
        What your OpenRouter accounts have spent, in tokens and in dollars, and what each token cost.
      </Text>
    </Stack>
  );

  if (accounts.length === 0) {
    return (
      <Stack gap="lg">
        {header}
        <Empty title="No OpenRouter account">
          Add an OpenRouter provider under{' '}
          <Anchor component={Link} to="/settings/providers">
            Settings
          </Anchor>{' '}
          and give it a management key, and its spend history appears here.
        </Empty>
      </Stack>
    );
  }
  if (!accounts.some((a) => a.has_management_key)) {
    return (
      <Stack gap="lg">
        {header}
        <Empty title="Spend history needs a management key">
          OpenRouter reports activity only to a management key, not to an ordinary API key. Add one to the OpenRouter
          provider under{' '}
          <Anchor component={Link} to="/settings/providers">
            Settings
          </Anchor>
          . It reads the whole account, every API key included, and Vibonomics keeps each day it sees from then on.
        </Empty>
      </Stack>
    );
  }

  const fetchNow = () =>
    refresh.mutate(['usage'], {
      onSuccess: () => notifications.show({ message: 'Fetching spend history…', color: 'blue' }),
      onError: (error) => notifications.show({ message: error.message, color: 'red' }),
    });

  const scope = providerId === null ? accounts : accounts.filter((a) => a.id === providerId);
  const synced = latest(scope.map((a) => a.last_synced ?? null));
  const filters = (
    <Group gap="md" align="flex-end" wrap="wrap">
      <SegmentedControl
        value={String(days)}
        onChange={(next) => setDays(Number(next))}
        data={RANGES}
        aria-label="Time range"
      />
      {accounts.length > 1 && (
        <Select
          label="Account"
          w={200}
          allowDeselect={false}
          data={[
            { value: 'all', label: 'All accounts' },
            ...accounts.map((a) => ({ value: String(a.id), label: a.name })),
          ]}
          value={providerId === null ? 'all' : String(providerId)}
          onChange={(next) => {
            setProviderId(next === null || next === 'all' ? null : Number(next));
            setKey(null);
          }}
        />
      )}
      {data.keys.length > 0 && (
        <Select
          label="API key"
          w={220}
          allowDeselect={false}
          data={[{ value: 'all', label: 'All keys' }, ...data.keys.map((k) => ({ value: k.hash, label: k.name }))]}
          value={key ?? 'all'}
          onChange={(next) => setKey(next === null || next === 'all' ? null : next)}
        />
      )}
      <Text size="sm" c="dimmed" ml="auto">
        {synced ? `Synced ${relativeTime(synced)}` : 'Not synced yet'}
      </Text>
    </Group>
  );

  if (data.daily.length === 0) {
    return (
      <Stack gap="lg">
        {header}
        {filters}
        <Empty title="No activity stored yet">
          Spend history is pulled from OpenRouter every few hours, and OpenRouter reports whole days that have finished.
          {canChange ? ' Fetch it now to see what is there.' : ' Check back after the next pull.'}
          {canChange && (
            <Group mt="sm">
              <Button loading={refresh.isPending} onClick={fetchNow}>
                Fetch spend history
              </Button>
            </Group>
          )}
        </Empty>
      </Stack>
    );
  }

  const t = data.totals;
  const refreshing = spend.isPlaceholderData;
  const modelRows: BreakdownRow[] = data.models.map((m) => ({ id: m.name, name: m.name, bucket: m }));
  const providerRows: BreakdownRow[] = data.upstreams.map((u) => ({ id: u.name, name: u.name, bucket: u }));
  const keyRows: BreakdownRow[] = data.keys.map((k) => ({
    id: k.hash,
    name: k.name,
    note: k.disabled ? 'Disabled' : k.last_used_at ? `Last used ${relativeTime(k.last_used_at)}` : 'Never used',
    bucket: k,
    onPick: () => setKey(k.hash),
  }));
  if (data.other_keys) {
    keyRows.push({
      id: 'other',
      name: 'Other',
      note: 'Spend no listed key accounts for: another workspace, or a key since deleted',
      bucket: data.other_keys,
    });
  }

  return (
    <Stack gap="lg">
      {header}
      {filters}

      <SimpleGrid cols={{ base: 2, sm: 3, lg: 5 }} spacing="md">
        <Tile
          label="Spend"
          value={money(t.cost)}
          sub={
            <>
              {dayLabel(data.since ?? '')} to {dayLabel(data.until ?? '')}
              {data.today_usd !== null && data.today_usd !== undefined && ` · ${money(data.today_usd)} so far today`}
            </>
          }
        />
        <Tile
          label="Tokens"
          value={bigCount(t.tokens)}
          sub={`${bigCount(t.prompt_tokens)} in, ${bigCount(t.completion_tokens)} out`}
        />
        <Tile
          label="Requests"
          value={integer(t.requests)}
          sub={
            t.cost_per_request === null || t.cost_per_request === undefined
              ? undefined
              : `${money(t.cost_per_request)} each`
          }
        />
        <Tile
          label="Cost per 1M tokens"
          value={t.cost_per_mtok === null || t.cost_per_mtok === undefined ? '—' : money(t.cost_per_mtok)}
          sub="Overall efficiency"
        />
        <Tile
          label="Cached"
          value={t.cache_hit === null || t.cache_hit === undefined ? '—' : percent(t.cache_hit)}
          sub="Of prompt tokens"
        />
      </SimpleGrid>

      <DailyCharts days={data.daily} refreshing={refreshing} />

      <BreakdownTable
        title="By model"
        description="The largest models by spend; the rest are folded into one row."
        nameHeader="Model"
        rows={modelRows}
        total={t.cost}
      />
      {key === null && keyRows.length > 0 && (
        <BreakdownTable
          title="By API key"
          description="Choose a key to see only its spend."
          nameHeader="Key"
          rows={keyRows}
          total={t.cost}
        />
      )}
      <BreakdownTable
        title="By provider"
        description="Who served the requests."
        nameHeader="Provider"
        rows={providerRows}
        total={t.cost}
      />

      <Alert color="gray" variant="light">
        <Text size="xs">
          OpenRouter reports the last 30 finished UTC days, so Vibonomics keeps each day it sees
          {scope.length > 0 && scope.some((a) => a.first_date)
            ? `; this history starts ${dateOnly(
                scope
                  .map((a) => a.first_date)
                  .filter(Boolean)
                  .sort()[0],
              )}`
            : ''}
          . Tokens are prompt plus completion, with reasoning counted inside completion. Cost per 1M tokens is spend
          divided by those, so a cache that serves most of the prompt pulls it down.
        </Text>
      </Alert>
    </Stack>
  );
}
