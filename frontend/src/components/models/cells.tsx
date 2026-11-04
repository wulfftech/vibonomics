import { Badge, Group, Progress, Stack, Text, Tooltip } from '@mantine/core';
import { Calculator, FlaskConical, ReceiptText } from 'lucide-react';
import type { ModelRow, UsageProfile } from '../../api/types';
import { DASH, discountLabel, money } from '../../lib/format';
import type { EnrichedModel } from '../../lib/modelTable';
import { discountTooltip, modelSubline, scoreColor, taskCostExplanation } from '../../lib/modelText';
import { blendedColor, profileFormula } from '../../lib/pricing';
import { providerColor } from '../../lib/providerColors';
import { tone } from '../../lib/tone';

export function ScoreNumber({ value, bold = false }: { value: number | null | undefined; bold?: boolean }) {
  if (value === null || value === undefined) {
    return (
      <Text size="sm" c="dimmed">
        {DASH}
      </Text>
    );
  }
  return (
    <Text size="sm" fw={bold ? 700 : 500} style={{ color: tone(scoreColor(value)) }} span>
      {Math.round(value)}
    </Text>
  );
}

/** The badges after a model's name: New, New discount, Price cut, Free, Inactive. Keep `modelBadgeLabels` in step. */
export function ModelBadges({ row }: { row: ModelRow }) {
  return (
    <>
      {row.is_new && (
        <Badge size="xs" color="blue" variant="light">
          New
        </Badge>
      )}
      {row.new_discount && (
        <Badge size="xs" color="orange" variant="light">
          New discount
        </Badge>
      )}
      {row.price_drop && (
        <Tooltip label="The price dropped recently" withArrow>
          <Badge size="xs" color="teal" variant="light">
            Price cut
          </Badge>
        </Tooltip>
      )}
      {row.is_free && (
        <Badge size="xs" color="green" variant="light">
          Free
        </Badge>
      )}
      {!row.active && (
        <Tooltip label="No longer listed by the provider" withArrow>
          <Badge size="xs" color="gray" variant="light">
            Inactive
          </Badge>
        </Tooltip>
      )}
    </>
  );
}

export function ModelNameCell({ row, alsoOnGo = false }: { row: ModelRow; alsoOnGo?: boolean }) {
  return (
    <Stack gap={0} miw={0}>
      <Group gap={6} wrap="nowrap" miw={0}>
        <Text size="sm" fw={500} truncate="end" title={row.name}>
          {row.name}
        </Text>
        <ModelBadges row={row} />
        {alsoOnGo && (
          <Tooltip
            label="OpenCode Go lists this model too. It carries the same per-token rate, but the Go plan meters it against your subscription allowance instead of billing it."
            multiline
            maw={320}
            withArrow
          >
            <Badge size="xs" color={providerColor('opencode-go')} variant="light">
              also on Go
            </Badge>
          </Tooltip>
        )}
      </Group>
      <Text size="xs" c="dimmed" truncate="end">
        {modelSubline(row) || DASH}
      </Text>
    </Stack>
  );
}

export function PriceTooltipBody({ model, profile }: { model: EnrichedModel; profile: UsageProfile }) {
  const { price } = model.row;
  return (
    <Stack gap={2}>
      <Text size="xs">Input {money(price.input)}</Text>
      <Text size="xs">Output {money(price.output)}</Text>
      <Text size="xs">Cache read {money(price.cache_read)}</Text>
      <Text size="xs">Cache write {money(price.cache_write)}</Text>
      <Text size="xs" mt={4}>
        {profile.name}: {profileFormula(profile)}
      </Text>
      {model.listBlended !== null && <Text size="xs">List price blended: {money(model.listBlended)}</Text>}
    </Stack>
  );
}

export function BlendedCell({ model, profile }: { model: EnrichedModel; profile: UsageProfile }) {
  if (model.blended === null) {
    return (
      <Text size="sm" c="dimmed">
        {DASH}
      </Text>
    );
  }
  return (
    <Tooltip label={<PriceTooltipBody model={model} profile={profile} />} withArrow>
      <Group gap={4} wrap="nowrap" style={{ cursor: 'help' }}>
        <Text size="sm" fw={600} style={{ color: blendedColor(model.blended, model.row.is_free) }}>
          {model.row.is_free ? 'Free' : money(model.blended)}
        </Text>
        {model.listBlended !== null && model.listBlended > model.blended && (
          <Text size="xs" c="dimmed">
            ({money(model.listBlended)})
          </Text>
        )}
      </Group>
    </Tooltip>
  );
}

/** The discount percentage in its own column, with the pricing note in a tooltip. */
export function DiscountCell({ row }: { row: ModelRow }) {
  if (!row.discount) {
    return (
      <Text size="sm" c="dimmed">
        {DASH}
      </Text>
    );
  }
  return (
    <Tooltip label={discountTooltip(row)} multiline maw={320} withArrow>
      <Badge size="sm" variant="light" color="green">
        {discountLabel(row.discount)}
      </Badge>
    </Tooltip>
  );
}

const COST_ICONS = {
  measured: FlaskConical,
  reported: ReceiptText,
  estimated: Calculator,
} as const;

export function TaskCostCell({ model, profile }: { model: EnrichedModel; profile: UsageProfile }) {
  if (model.taskCost === null || model.taskCostSource === null) {
    return (
      <Text size="sm" c="dimmed">
        {DASH}
      </Text>
    );
  }
  const Icon = COST_ICONS[model.taskCostSource];
  return (
    <Tooltip label={taskCostExplanation(model, profile)} multiline maw={300} withArrow>
      <Group gap={4} wrap="nowrap" style={{ cursor: 'help' }}>
        <Icon
          size={12}
          aria-label={model.taskCostSource}
          color={
            model.taskCostSource === 'measured'
              ? 'var(--mantine-color-grape-filled)'
              : model.taskCostSource === 'reported'
                ? 'var(--mantine-color-blue-filled)'
                : 'var(--mantine-color-dimmed)'
          }
        />
        <Text size="sm">{money(model.taskCost)}</Text>
      </Group>
    </Tooltip>
  );
}

export function CompositeCell({ row }: { row: ModelRow }) {
  const { composite, source_count: sources, own_test: ownTest } = row.scores;
  if (composite === null || composite === undefined) {
    return (
      <Text size="sm" c="dimmed">
        {DASH}
      </Text>
    );
  }
  return (
    <Group gap={6} wrap="nowrap">
      <Tooltip label={`Composite of ${sources} source${sources === 1 ? '' : 's'}`} withArrow>
        <Group gap={3} wrap="nowrap" style={{ cursor: 'help' }}>
          <ScoreNumber value={composite} bold />
          <Text size="xs" c="dimmed" span>
            ·{sources}
          </Text>
        </Group>
      </Tooltip>
      {ownTest !== null && ownTest !== undefined && (
        <Tooltip label="Average score in Vibonomics's own tests" withArrow>
          <Badge size="xs" color="grape" variant="light">
            tested {Math.round(ownTest)}
          </Badge>
        </Tooltip>
      )}
    </Group>
  );
}

export function ValueCell({ model }: { model: EnrichedModel }) {
  if (model.valueIndex === null) {
    return (
      <Text size="sm" c="dimmed">
        {DASH}
      </Text>
    );
  }
  const index = Math.round(model.valueIndex);
  const basis =
    model.assumedFreeCost !== null
      ? ` Free models are costed at ${money(model.assumedFreeCost)}/1M for this ranking (Settings → Scoring).`
      : '';
  return (
    <Tooltip
      label={`${model.value?.toFixed(1) ?? DASH} score points per $ per task (index ${index} of 100, log scale).${basis}`}
      multiline
      maw={320}
      withArrow
    >
      <Stack gap={3} w={64} style={{ cursor: 'help' }}>
        <Text size="sm" fw={500} lh={1}>
          {index}
        </Text>
        <Progress value={index} size={3} color={scoreColor(index)} aria-label={`Value index ${index}`} />
      </Stack>
    </Tooltip>
  );
}
