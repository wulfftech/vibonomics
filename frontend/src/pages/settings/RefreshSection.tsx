import { Card, NumberInput, SimpleGrid, Stack, Switch, Text, Title } from '@mantine/core';
import { ErrorState, LoadingState } from '../../components/DataState';
import { SaveBar } from './SaveBar';
import { toNumber, useSettingsDraft } from './useSettingsDraft';

export function RefreshSection() {
  const { query, value, update, dirty, submit, saving, reset } = useSettingsDraft(['refresh', 'highlight', 'general']);

  if (query.isPending) {
    return <LoadingState />;
  }
  if (query.isError || value === null) {
    return <ErrorState message={query.error?.message} />;
  }
  const { refresh, highlight, general } = value;

  const setRefresh = (patch: Partial<typeof refresh>) =>
    update((old) => ({ ...old, refresh: { ...old.refresh, ...patch } }));
  const setHighlight = (patch: Partial<typeof highlight>) =>
    update((old) => ({ ...old, highlight: { ...old.highlight, ...patch } }));

  return (
    <Stack gap="lg">
      <Stack gap={4}>
        <Title order={3}>Refresh</Title>
        <Text size="sm" c="dimmed">
          How often Vibonomics re-reads model lists, prices, balances and leaderboards. 0 turns a schedule off; the
          Refresh button on the Models page still works.
        </Text>
      </Stack>

      <Card withBorder padding="md">
        <Stack gap="md">
          <Title order={4}>Schedules</Title>
          <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="md">
            <NumberInput
              label="Models & prices"
              description="Hours between refreshes"
              min={0}
              step={1}
              decimalScale={2}
              suffix=" h"
              value={refresh.models_interval_hours}
              onChange={(next) => setRefresh({ models_interval_hours: toNumber(next) })}
            />
            <NumberInput
              label="Balances"
              description="Minutes between refreshes"
              min={0}
              step={5}
              suffix=" min"
              value={refresh.balances_interval_minutes}
              onChange={(next) => setRefresh({ balances_interval_minutes: toNumber(next) })}
            />
            <NumberInput
              label="Benchmarks"
              description="Hours between refreshes"
              min={0}
              step={1}
              decimalScale={2}
              suffix=" h"
              value={refresh.benchmarks_interval_hours}
              onChange={(next) => setRefresh({ benchmarks_interval_hours: toNumber(next) })}
            />
          </SimpleGrid>
          <Switch
            label="Refresh on startup"
            description="Fetch everything a few seconds after Vibonomics starts"
            checked={refresh.refresh_on_startup}
            onChange={(event) => setRefresh({ refresh_on_startup: event.currentTarget.checked })}
          />
          <Switch
            label="Check OpenRouter endpoint discounts"
            description="Queries OpenRouter's per-provider endpoints to spot discounts and list each model's upstream providers. One extra request per paid model on every models refresh."
            checked={refresh.openrouter_endpoint_discounts}
            onChange={(event) => setRefresh({ openrouter_endpoint_discounts: event.currentTarget.checked })}
          />
        </Stack>
      </Card>

      <Card withBorder padding="md">
        <Stack gap="md">
          <Title order={4}>Highlights</Title>
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <NumberInput
              label="New models"
              description="Days a model shows the New badge after it first appears or is released"
              min={0}
              max={365}
              suffix=" days"
              value={highlight.new_model_days}
              onChange={(next) => setHighlight({ new_model_days: Math.round(toNumber(next)) })}
            />
            <NumberInput
              label="New discounts and price cuts"
              description="Days a fresh discount or price cut stays highlighted"
              min={0}
              max={365}
              suffix=" days"
              value={highlight.new_discount_days}
              onChange={(next) => setHighlight({ new_discount_days: Math.round(toNumber(next)) })}
            />
          </SimpleGrid>
          <Switch
            label="Hide inactive models"
            description="Models a provider no longer lists are hidden from the Models page"
            checked={general.hide_inactive_models}
            onChange={(event) => {
              const checked = event.currentTarget.checked;
              update((old) => ({ ...old, general: { ...old.general, hide_inactive_models: checked } }));
            }}
          />
        </Stack>
      </Card>

      <SaveBar dirty={dirty} saving={saving} onReset={reset} onSave={() => submit()} />
    </Stack>
  );
}
