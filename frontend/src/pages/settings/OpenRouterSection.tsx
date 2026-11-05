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
import { useApplyPreset, usePresetList, usePresetPlan, usePresetState } from '../../api/presets';
import { ErrorState, LoadingState } from '../../components/DataState';
import { dateTime, relativeTime } from '../../lib/format';
import { PRESET_PREFIX, presetName, presetSlug } from '../../lib/presets';
import { PresetPlanView } from './PresetPlanView';
import { SaveBar } from './SaveBar';
import { toNumber, useSettingsDraft } from './useSettingsDraft';

const DIMENSIONS = [
  { value: 'composite', label: 'Overall (composite)' },
  { value: 'intelligence', label: 'Intelligence' },
  { value: 'coding', label: 'Coding' },
  { value: 'agentic', label: 'Agentic' },
];

/** OpenRouter refuses a preset that lists more models than this (HTTP 400). */
const MAX_MODELS = 3;

const TEMPLATE_HELP = 'Its tools, reasoning and provider filters are copied into a new preset. Read only.';

/** The account's own presets to start from. Falls back to typing a slug when OpenRouter cannot be asked. */
function TemplatePicker({ value, onChange }: { value: string | null; onChange: (slug: string | null) => void }) {
  const list = usePresetList();
  const presets = list.data?.presets ?? [];
  const problem = list.isError ? list.error.message : (list.data?.error ?? null);

  if (problem !== null) {
    return (
      <TextInput
        label="Start from preset (optional)"
        description={`${TEMPLATE_HELP} The list of your presets is unavailable: ${problem}`}
        placeholder="my-preset"
        value={value ?? ''}
        onChange={(event) => onChange(event.currentTarget.value)}
      />
    );
  }

  const data = presets.map((p) => ({ value: p.slug, label: p.name === p.slug ? p.slug : `${p.name} (${p.slug})` }));
  if (value && !data.some((d) => d.value === value)) {
    data.unshift({ value, label: value });
  }
  const bySlug = new Map(presets.map((p) => [p.slug, p]));
  return (
    <Select
      label="Start from preset (optional)"
      description={TEMPLATE_HELP}
      placeholder={
        list.isPending ? 'Loading your presets…' : presets.length > 0 ? 'None' : 'No presets on your account'
      }
      searchable
      clearable
      data={data}
      value={value}
      onChange={onChange}
      renderOption={({ option }) => {
        const p = bySlug.get(option.value);
        return (
          <Stack gap={0}>
            <Text size="sm">{option.label}</Text>
            {p !== undefined && (
              <Text size="xs" c="dimmed">
                {[
                  p.models.length > 0 ? p.models.join(', ') : 'no model set',
                  p.providers.length > 0 ? `only ${p.providers.join(', ')}` : null,
                  p.version === null || p.version === undefined ? null : `v${p.version}`,
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </Text>
            )}
          </Stack>
        );
      }}
    />
  );
}

/** Settings that apply to OpenRouter alone. For now that is the best-value preset. */
export function OpenRouterSection() {
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
  const fullSlug = presetSlug(presets.slug);
  const nameMissing = fullSlug === '';

  // Unsaved changes are previewed as they stand, so they can be tried before they are kept.
  const preview = () => plan.mutate(dirty ? presets : undefined);
  const send = (digest: string) => {
    apply.mutate(digest, {
      onSuccess: (result) => {
        notifications.show({
          message: result.message,
          color: result.unsaved.length > 0 ? 'yellow' : result.applied ? 'green' : 'blue',
          autoClose: result.unsaved.length > 0 ? 12000 : 5000,
        });
        plan.mutate(undefined);
      },
      onError: (error) => {
        notifications.show({ message: error.message, color: 'red' });
        plan.mutate(undefined);
      },
    });
  };
  const applyPlan = () => {
    const digest = plan.data?.digest;
    if (digest) {
      send(digest);
    }
  };
  // Saving with writing allowed sends the update right away, so a change reaches OpenRouter without a second step.
  // It plans from what was just saved, and sends nothing when that plan has nothing to write.
  const pushSaved = () =>
    plan.mutate(undefined, {
      onSuccess: (fresh) => {
        if (fresh.draft) {
          return;
        }
        if (!['create', 'update'].includes(fresh.action) || !fresh.digest) {
          notifications.show({ message: fresh.reason, color: fresh.action === 'blocked' ? 'yellow' : 'blue' });
          return;
        }
        send(fresh.digest);
      },
      onError: (error) => notifications.show({ message: error.message, color: 'red' }),
    });

  const current = plan.data;
  // Apply plans from what is saved, so only a plan made from saved settings can be applied.
  const canApply =
    current !== undefined &&
    ['create', 'update'].includes(current.action) &&
    presets.enabled &&
    !dirty &&
    !current.draft;
  const last = state.data;

  return (
    <Stack gap="lg">
      <Stack gap={4}>
        <Title order={3}>OpenRouter</Title>
        <Text size="sm" c="dimmed">
          Settings that apply to OpenRouter only. Its keys and models are under All providers.
        </Text>
      </Stack>

      <Stack gap={4}>
        <Title order={4}>Best-value preset</Title>
        <Text size="sm" c="dimmed">
          Keeps one OpenRouter preset pointed at the models that give the most score per dollar, so any client that
          cannot pin a provider can use <code>@preset/{fullSlug || '…'}</code> as its model and follow the best value.
          Each update is a new version of the preset. Vibonomics only writes a preset it created itself, and never
          deletes one.
        </Text>
      </Stack>

      <Card withBorder padding="md">
        <Stack gap="md">
          <Switch
            label="Let Vibonomics write this preset to my OpenRouter account"
            description="Off by default. With it off you can still preview exactly what would be written."
            checked={presets.enabled}
            onChange={(event) => set({ enabled: event.currentTarget.checked })}
          />
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <TextInput
              label="Preset name"
              description={
                <>
                  It becomes <code>@preset/{fullSlug || '…'}</code>. The <code>{PRESET_PREFIX}</code> prefix is added
                  for you: it is how Vibonomics knows a preset is its own, so one you made by hand is never touched.
                </>
              }
              leftSection={
                <Text size="sm" c="dimmed">
                  {PRESET_PREFIX}
                </Text>
              }
              leftSectionWidth={92}
              leftSectionPointerEvents="none"
              value={presetName(presets.slug)}
              error={nameMissing ? 'Give the preset a name' : undefined}
              onChange={(event) => set({ slug: event.currentTarget.value })}
            />
            <TemplatePicker value={presets.template_slug ?? null} onChange={(slug) => set({ template_slug: slug })} />
          </SimpleGrid>
          <Title order={5}>How the models are chosen</Title>
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
              label="Models listed"
              description="Best value first; the rest are fallbacks. 1 to 3, OpenRouter's limit"
              min={1}
              max={MAX_MODELS}
              value={presets.max_models}
              onChange={(next) => set({ max_models: Math.min(MAX_MODELS, Math.max(1, Math.round(toNumber(next, 1)))) })}
            />
            <NumberInput
              label="Providers listed"
              description="Cheapest first, shared across the models. 1 to 10"
              min={1}
              max={10}
              value={presets.max_providers}
              onChange={(next) => set({ max_providers: Math.min(10, Math.max(1, Math.round(toNumber(next, 1)))) })}
            />
            <NumberInput
              label="Switch only for a gain of"
              description="Percent more value than a model already listed"
              min={0}
              max={100}
              suffix=" %"
              value={presets.switch_margin_pct}
              onChange={(next) => set({ switch_margin_pct: toNumber(next) })}
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
              label="Maximum cost per task"
              description="Most a model may cost for one task of the usage profile. Empty for no limit"
              placeholder="No limit"
              min={0}
              step={0.01}
              decimalScale={4}
              prefix="$"
              value={presets.max_task_cost ?? ''}
              onChange={(next) => set({ max_task_cost: next === '' ? null : toNumber(next) })}
            />
            <NumberInput
              label="Minimum value"
              description="Score points per dollar of one task, the figure in the preview. Empty for no limit"
              placeholder="No limit"
              min={0}
              step={100}
              thousandSeparator=","
              value={presets.min_value ?? ''}
              onChange={(next) => set({ min_value: next === '' ? null : toNumber(next) })}
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
              description="0.5 is every 30 minutes"
              w={200}
              min={0.5}
              max={168}
              step={0.5}
              decimalScale={1}
              suffix=" h"
              disabled={!presets.enabled || !presets.auto_apply}
              value={presets.interval_hours}
              onChange={(next) => set({ interval_hours: toNumber(next, 24) })}
            />
          </Group>
        </Stack>
      </Card>
      {dirty && presets.enabled && (
        <Text size="xs" c="dimmed" ta="right">
          Saving also sends the update to OpenRouter.
        </Text>
      )}
      <SaveBar
        dirty={dirty && !nameMissing}
        saving={saving}
        onReset={reset}
        onSave={() => submit({ onSaved: presets.enabled ? pushSaved : undefined })}
      />

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
              <Button variant="default" loading={plan.isPending} disabled={nameMissing} onClick={preview}>
                Preview
              </Button>
              <Button loading={apply.isPending} disabled={!canApply} onClick={applyPlan}>
                Apply this plan
              </Button>
            </Group>
          </Group>
          {dirty && (
            <Alert color="blue" variant="light" py="xs">
              You have unsaved changes. Preview tries them out without saving them; save them to be able to apply.
            </Alert>
          )}
          {!dirty && current !== undefined && !presets.enabled && ['create', 'update'].includes(current.action) && (
            <Alert color="blue" variant="light" py="xs">
              Turn on &ldquo;Let Vibonomics write this preset&rdquo; and save to be able to apply it.
            </Alert>
          )}
          {!dirty && current?.draft === true && (
            <Alert color="blue" variant="light" py="xs">
              This preview was made from settings that were not saved. Preview again to be able to apply.
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
