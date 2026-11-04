import {
  Alert,
  Button,
  Card,
  Group,
  NumberInput,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useApplyPreset, usePresetPlan, usePresetState } from '../../api/presets';
import { ErrorState, LoadingState } from '../../components/DataState';
import { dateTime, relativeTime } from '../../lib/format';
import { PresetPlanView } from './PresetPlanView';
import { SaveBar } from './SaveBar';
import { toNumber, useSettingsDraft } from './useSettingsDraft';

const DIMENSIONS = [
  { value: 'composite', label: 'Overall (composite)' },
  { value: 'intelligence', label: 'Intelligence' },
  { value: 'coding', label: 'Coding' },
  { value: 'agentic', label: 'Agentic' },
];

export function PresetsSection() {
  const { query, value, update, dirty, submit, saving, reset } = useSettingsDraft(['presets']);
  const state = usePresetState();
  const plan = usePresetPlan();
  const apply = useApplyPreset();

  if (query.isPending) {
    return <LoadingState />;
  }
  if (query.isError || value === null) {
    return <ErrorState message={query.error?.message} />;
  }
  const { presets, scoring } = value;
  const set = (patch: Partial<typeof presets>) => update((old) => ({ ...old, presets: { ...old.presets, ...patch } }));

  const preview = () => plan.mutate();
  const applyPlan = () => {
    const digest = plan.data?.digest;
    if (!digest) {
      return;
    }
    apply.mutate(digest, {
      onSuccess: (result) => {
        notifications.show({
          message: result.message,
          color: result.unsaved.length > 0 ? 'yellow' : result.applied ? 'green' : 'blue',
          autoClose: result.unsaved.length > 0 ? 12000 : 5000,
        });
        plan.mutate();
      },
      onError: (error) => {
        notifications.show({ message: error.message, color: 'red' });
        plan.mutate();
      },
    });
  };

  const current = plan.data;
  const canApply = current !== undefined && ['create', 'update'].includes(current.action) && presets.enabled && !dirty;
  const last = state.data;

  return (
    <Stack gap="lg">
      <Stack gap={4}>
        <Title order={3}>Best-value preset</Title>
        <Text size="sm" c="dimmed">
          Keeps one OpenRouter preset pointed at the model that gives the most score per dollar, so any client that
          cannot pin a provider can use <code>@preset/{presets.slug}</code> as its model and follow the best value. Each
          update is a new version of the preset. Vibonomics only writes a preset it created itself, named{' '}
          <code>vibonomics-…</code>, and never deletes one.
        </Text>
      </Stack>

      <Card withBorder padding="md">
        <Stack gap="md">
          <Title order={4}>Settings</Title>
          <Switch
            label="Let Vibonomics write this preset to my OpenRouter account"
            description="Off by default. With it off you can still preview exactly what would be written."
            checked={presets.enabled}
            onChange={(event) => set({ enabled: event.currentTarget.checked })}
          />
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <TextInput
              label="Preset slug"
              description="Starts with vibonomics-. A preset of that name you made by hand is never touched."
              value={presets.slug}
              onChange={(event) => set({ slug: event.currentTarget.value })}
            />
            <TextInput
              label="Start from preset (optional)"
              description="Its tools, reasoning and provider filters are copied into a new preset. Read only."
              placeholder="my-preset"
              value={presets.template_slug ?? ''}
              onChange={(event) => set({ template_slug: event.currentTarget.value })}
            />
          </SimpleGrid>
          <Title order={5}>How the model is chosen</Title>
          <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }} spacing="md">
            <Select
              label="Costed for"
              description="The usage profile that sets the size of a task"
              allowDeselect={false}
              data={scoring.profiles.map((p) => ({ value: p.id, label: p.name || p.id }))}
              value={presets.profile ?? scoring.default_profile ?? null}
              onChange={(next) => set({ profile: next })}
            />
            <Select
              label="Score"
              description="Which score counts"
              allowDeselect={false}
              data={DIMENSIONS}
              value={presets.dimension}
              onChange={(next) => set({ dimension: (next ?? 'composite') as typeof presets.dimension })}
            />
            <NumberInput
              label="Minimum score"
              description="0 to 100"
              min={0}
              max={100}
              value={presets.min_score}
              onChange={(next) => set({ min_score: toNumber(next) })}
            />
            <NumberInput
              label="Minimum context"
              description="Tokens; 0 for any"
              min={0}
              step={32_000}
              thousandSeparator=","
              value={presets.min_context}
              onChange={(next) => set({ min_context: Math.round(toNumber(next)) })}
            />
            <NumberInput
              label="Switch only for a gain of"
              description="Percent more value than the current model"
              min={0}
              max={100}
              suffix=" %"
              value={presets.switch_margin_pct}
              onChange={(next) => set({ switch_margin_pct: toNumber(next) })}
            />
            <NumberInput
              label="Providers listed"
              description="Cheapest first, 1 to 5"
              min={1}
              max={5}
              value={presets.max_providers}
              onChange={(next) => set({ max_providers: Math.min(5, Math.max(1, Math.round(toNumber(next, 1)))) })}
            />
          </SimpleGrid>
          <Group gap="xl">
            <Switch
              label="Needs tool calling"
              checked={presets.require_tool_call}
              onChange={(event) => set({ require_tool_call: event.currentTarget.checked })}
            />
            <Switch
              label="Allow free models"
              checked={presets.include_free}
              onChange={(event) => set({ include_free: event.currentTarget.checked })}
            />
          </Group>
          <Title order={5}>Unattended updates</Title>
          <Group align="flex-end" gap="xl">
            <Switch
              label="Update the preset on a schedule"
              description="Needs the switch at the top. Preview and apply it by hand first."
              disabled={!presets.enabled}
              checked={presets.enabled && presets.auto_apply}
              onChange={(event) => set({ auto_apply: event.currentTarget.checked })}
            />
            <NumberInput
              label="Every"
              w={140}
              min={1}
              max={168}
              suffix=" h"
              disabled={!presets.enabled || !presets.auto_apply}
              value={presets.interval_hours}
              onChange={(next) => set({ interval_hours: toNumber(next, 24) })}
            />
          </Group>
        </Stack>
      </Card>
      <SaveBar dirty={dirty} saving={saving} onReset={reset} onSave={() => submit()} />

      <Card withBorder padding="md">
        <Stack gap="md">
          <Group justify="space-between" align="flex-start">
            <Stack gap={0}>
              <Title order={4}>Preview</Title>
              <Text size="sm" c="dimmed">
                A dry run with the saved settings. It reads your preset from OpenRouter and writes nothing.
              </Text>
            </Stack>
            <Group gap="xs">
              <Button variant="default" loading={plan.isPending} disabled={dirty} onClick={preview}>
                Preview
              </Button>
              <Button loading={apply.isPending} disabled={!canApply} onClick={applyPlan}>
                Apply this plan
              </Button>
            </Group>
          </Group>
          {dirty && (
            <Alert color="blue" variant="light" py="xs">
              Save your changes first: the preview uses the saved settings.
            </Alert>
          )}
          {!dirty && current !== undefined && !presets.enabled && ['create', 'update'].includes(current.action) && (
            <Alert color="blue" variant="light" py="xs">
              Turn on &ldquo;Let Vibonomics write this preset&rdquo; and save to be able to apply it.
            </Alert>
          )}
          {plan.isError && <ErrorState message={plan.error.message} />}
          {current !== undefined && <PresetPlanView plan={current} />}
        </Stack>
      </Card>

      {last !== undefined && last.last_run_at !== null && last.last_run_at !== undefined && (
        <Card withBorder padding="md">
          <Stack gap={4}>
            <Title order={4}>Last run</Title>
            <Text size="sm" title={dateTime(last.last_run_at)}>
              {relativeTime(last.last_run_at)} ({last.last_trigger}): {last.last_status}
            </Text>
            <Text size="sm" c="dimmed">
              {last.last_message}
            </Text>
            {last.version !== null && last.version !== undefined && (
              <Text size="sm" c="dimmed">
                Last written: {last.slug} v{last.version} on {last.model_id}, {relativeTime(last.applied_at)}.
              </Text>
            )}
          </Stack>
        </Card>
      )}
    </Stack>
  );
}
