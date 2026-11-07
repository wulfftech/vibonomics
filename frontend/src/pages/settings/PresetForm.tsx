import { Card, Group, NumberInput, Select, SimpleGrid, Stack, Switch, Text, TextInput, Title } from '@mantine/core';
import type { FullSettings, PresetSettings } from '../../api/types';
import { itemSlug, PRESET_PREFIX, presetName } from '../../lib/presets';
import { BlocklistField } from './BlocklistField';
import { TemplatePicker, type TemplateSource } from './TemplatePicker';
import { toNumber } from './useSettingsDraft';

const DIMENSIONS = [
  { value: 'composite', label: 'Overall (composite)' },
  { value: 'intelligence', label: 'Intelligence' },
  { value: 'coding', label: 'Coding' },
  { value: 'agentic', label: 'Agentic' },
];

const ROUTES = [
  { value: 'fallback', label: 'Fallback: try the models in order' },
  { value: 'sort', label: 'Sort: let OpenRouter order the models by price, throughput or latency' },
];

const SORTS = [
  { value: 'price', label: 'Price' },
  { value: 'throughput', label: 'Throughput' },
  { value: 'latency', label: 'Latency' },
];

const OWN_TESTS = [
  { value: 'follow', label: 'Follow the global setting' },
  { value: 'prefer', label: 'Prefer my own tests' },
  { value: 'blend', label: 'Use the blend' },
];

/** OpenRouter refuses a preset that lists more models than this (HTTP 400). */
const MAX_MODELS = 3;

// The form sits in a column beside the list, so its columns follow the width of that column, not of the window.
const TWO_COLUMNS = { base: 1, '34rem': 2 };
const THREE_COLUMNS = { base: 1, '34rem': 2, '56rem': 3 };

type Dimension = PresetSettings['dimension'];

function ownTestsValue(preference: boolean | null | undefined): string {
  return preference === null || preference === undefined ? 'follow' : preference ? 'prefer' : 'blend';
}

/** Everything one managed preset can be told: what it may write, how it chooses, how it routes, and when it runs. */
export function PresetForm({
  item,
  onChange,
  scoring,
  templates,
}: {
  item: PresetSettings;
  onChange: (patch: Partial<PresetSettings>) => void;
  scoring: FullSettings['scoring'];
  templates: TemplateSource;
}) {
  const fullSlug = itemSlug(item);
  const sorted = item.route === 'sort';
  return (
    <Card withBorder padding="md">
      <Stack gap="md">
        <Switch
          label="Let Vibonomics write this preset to my OpenRouter account"
          description="Off by default. With it off you can still preview exactly what would be written."
          checked={item.enabled}
          onChange={(event) => onChange({ enabled: event.currentTarget.checked })}
        />
        <SimpleGrid type="container" cols={TWO_COLUMNS} spacing="md">
          <TextInput
            label="Preset name"
            readOnly
            description={
              item.adopted ? (
                'Adopted from your account, so its name is kept exactly as it is.'
              ) : (
                <>
                  It becomes <code>@preset/{fullSlug}</code>. The <code>{PRESET_PREFIX}</code> prefix is added for you:
                  it is how Vibonomics knows a preset is its own, so one you made by hand is never touched. The name
                  stays as it is once the preset is added; start a new preset to use another.
                </>
              )
            }
            leftSection={
              item.adopted ? undefined : (
                <Text size="sm" c="dimmed">
                  {PRESET_PREFIX}
                </Text>
              )
            }
            leftSectionWidth={item.adopted ? undefined : 92}
            leftSectionPointerEvents="none"
            value={item.adopted ? item.slug : presetName(item.slug)}
          />
          <TemplatePicker
            source={templates}
            value={item.template_slug ?? null}
            onChange={(slug) => onChange({ template_slug: slug })}
          />
        </SimpleGrid>

        <Title order={5}>How the models are chosen</Title>
        <SimpleGrid type="container" cols={THREE_COLUMNS} spacing="md">
          <Select
            label="Costed for"
            description="The usage profile that sets the size of a task"
            allowDeselect={false}
            data={scoring.profiles.map((p) => ({ value: p.id, label: p.name || p.id }))}
            value={item.profile ?? scoring.default_profile ?? null}
            onChange={(next) => onChange({ profile: next })}
          />
          <Select
            label="Score"
            description="Which score counts"
            allowDeselect={false}
            data={DIMENSIONS}
            value={item.dimension}
            onChange={(next) => onChange({ dimension: (next ?? 'composite') as Dimension })}
          />
          <NumberInput
            label="Minimum score"
            description="0 to 100"
            min={0}
            max={100}
            value={item.min_score}
            onChange={(next) => onChange({ min_score: toNumber(next) })}
          />
          <NumberInput
            label="Models listed"
            description="Best value first; the rest are fallbacks. 1 to 3, OpenRouter's limit"
            min={1}
            max={MAX_MODELS}
            value={item.max_models}
            onChange={(next) =>
              onChange({ max_models: Math.min(MAX_MODELS, Math.max(1, Math.round(toNumber(next, 1)))) })
            }
          />
          <NumberInput
            label="Providers listed"
            description="Cheapest first, shared across the models; each model keeps at least one. 1 to 10"
            min={1}
            max={10}
            value={item.max_providers}
            onChange={(next) => onChange({ max_providers: Math.min(10, Math.max(1, Math.round(toNumber(next, 1)))) })}
          />
          <NumberInput
            label="Switch only for a gain of"
            description="Percent more value than a model already listed"
            min={0}
            max={100}
            suffix=" %"
            value={item.switch_margin_pct}
            onChange={(next) => onChange({ switch_margin_pct: toNumber(next) })}
          />
          <NumberInput
            label="Minimum context"
            description="Tokens; 0 for any"
            min={0}
            step={32_000}
            thousandSeparator=","
            value={item.min_context}
            onChange={(next) => onChange({ min_context: Math.round(toNumber(next)) })}
          />
          <NumberInput
            label="Maximum cost per task"
            description="Most a model may cost for one task of the usage profile. Empty for no limit"
            placeholder="No limit"
            min={0}
            step={0.01}
            decimalScale={4}
            prefix="$"
            value={item.max_task_cost ?? ''}
            onChange={(next) => onChange({ max_task_cost: next === '' ? null : toNumber(next) })}
          />
          <NumberInput
            label="Minimum value"
            description="Score points per dollar of one task, the figure in the preview. Empty for no limit"
            placeholder="No limit"
            min={0}
            step={100}
            thousandSeparator=","
            value={item.min_value ?? ''}
            onChange={(next) => onChange({ min_value: next === '' ? null : toNumber(next) })}
          />
          <Select
            label="Own test scores"
            description="Whether a model's own test result replaces its blended score when ranking"
            allowDeselect={false}
            data={OWN_TESTS}
            value={ownTestsValue(item.prefer_own_tests)}
            onChange={(next) => onChange({ prefer_own_tests: next === 'follow' ? null : next === 'prefer' })}
          />
        </SimpleGrid>
        <Group gap="xl">
          <Switch
            label="Needs tool calling"
            checked={item.require_tool_call}
            onChange={(event) => onChange({ require_tool_call: event.currentTarget.checked })}
          />
          <Switch
            label="Allow free models"
            description="Free models share one daily request cap across your account."
            checked={item.include_free}
            onChange={(event) => onChange({ include_free: event.currentTarget.checked })}
          />
        </Group>
        <Stack gap="xs" pl="md">
          <Switch
            label="Keep a paid model as the last fallback"
            description={
              item.include_free && item.paid_fallback && item.max_models < 2
                ? 'Needs room for two or more models: raise Models listed.'
                : 'When every model chosen is free, the last place goes to the best-value paid model, so the preset keeps working once the free cap is spent.'
            }
            disabled={!item.include_free}
            checked={item.paid_fallback}
            onChange={(event) => onChange({ paid_fallback: event.currentTarget.checked })}
          />
          <Switch
            label="Allow a lineup of free models only"
            description="Off: when no paid model makes the lineup, the update is refused and nothing is written."
            disabled={!item.include_free}
            checked={item.allow_free_only}
            onChange={(event) => onChange({ allow_free_only: event.currentTarget.checked })}
          />
        </Stack>

        <Title order={5}>Routing</Title>
        <SimpleGrid type="container" cols={TWO_COLUMNS} spacing="md">
          <Select
            label="Routing"
            description="OpenRouter does not document this option: after a write the app tells you if it did not keep it"
            allowDeselect={false}
            data={ROUTES}
            value={item.route}
            onChange={(next) => onChange({ route: next === 'sort' ? 'sort' : 'fallback' })}
          />
          {sorted && (
            <Select
              label="Order the models by"
              description="Price when nothing else is chosen"
              allowDeselect={false}
              data={SORTS}
              value={item.sort ?? 'price'}
              onChange={(next) => onChange({ sort: next === 'throughput' || next === 'latency' ? next : 'price' })}
            />
          )}
        </SimpleGrid>

        <Title order={5}>Blocklists</Title>
        <SimpleGrid type="container" cols={TWO_COLUMNS} spacing="md">
          <BlocklistField
            label="Blocked models for this preset"
            description="Patterns such as openai/* or *:free. Added to the global lists"
            placeholder="openai/*"
            value={item.blocked_models ?? []}
            onChange={(blocked_models) => onChange({ blocked_models })}
          />
          <BlocklistField
            label="Blocked providers for this preset"
            description="Patterns such as deepinfra. Added to the global lists"
            placeholder="deepinfra"
            value={item.blocked_providers ?? []}
            onChange={(blocked_providers) => onChange({ blocked_providers })}
          />
        </SimpleGrid>

        <Title order={5}>Unattended updates</Title>
        <Group align="flex-end" gap="xl">
          <Switch
            label="Update the preset on a schedule"
            description="Needs the switch at the top. Preview and apply it by hand first."
            disabled={!item.enabled}
            checked={item.enabled && item.auto_apply}
            onChange={(event) => onChange({ auto_apply: event.currentTarget.checked })}
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
            disabled={!item.enabled || !item.auto_apply}
            value={item.interval_hours}
            onChange={(next) => onChange({ interval_hours: toNumber(next, 24) })}
          />
        </Group>
      </Stack>
    </Card>
  );
}
