import { Alert, Stack } from '@mantine/core';
import type { ReactNode } from 'react';
import type { FullSettings, PresetSettings, PresetSlugState } from '../../api/types';
import { hasGuardrailProblems, livePreset, type PresetEntry } from '../../lib/presets';
import { GuardrailsCard } from './GuardrailsCard';
import { PresetForm } from './PresetForm';
import { PresetHeader } from './PresetHeader';
import { LastRun, PresetPreview } from './PresetPreview';
import { StopManaging } from './StopManaging';
import type { TemplateSource } from './TemplatePicker';
import type { PresetUpdate } from './usePresetUpdate';

/** The page of a preset Vibonomics manages: its settings, the Save bar, what a run would do, and the last run. */
export function PresetEditor({
  entry,
  item,
  scoring,
  templates,
  unsaved,
  last,
  update,
  onChange,
  onStop,
  saveBar,
}: {
  entry: PresetEntry;
  item: PresetSettings;
  scoring: FullSettings['scoring'];
  templates: TemplateSource;
  /** The settings differ from what is saved, or were never saved. */
  unsaved: boolean;
  last: PresetSlugState | undefined;
  update: PresetUpdate;
  onChange: (patch: Partial<PresetSettings>) => void;
  onStop: () => void;
  saveBar: ReactNode;
}) {
  const plan = update.plan.data?.slug === entry.slug ? update.plan.data : undefined;
  // A Set guardrail without a usable value is never sent, so there is nothing to preview until it is fixed.
  const canPreview = !hasGuardrailProblems(item.guardrails);
  return (
    <Stack gap="lg">
      <PresetHeader entry={entry} mark presetId={plan?.preset_id} workspace={plan?.workspace} />
      {item.adopted && (
        <Alert color="blue" variant="light" py="xs">
          You adopted this preset. A scheduled run writes to it only while the keys Vibonomics sends back unchanged are
          the ones you reviewed; if they changed on OpenRouter the run stops and says so, and nothing is written until
          you apply a plan by hand.
        </Alert>
      )}
      <PresetForm item={item} onChange={onChange} scoring={scoring} templates={templates} />
      <GuardrailsCard
        guardrails={item.guardrails}
        live={livePreset(entry.catalog, plan)}
        onChange={(guardrails) => onChange({ guardrails })}
      />
      {saveBar}
      <StopManaging slug={entry.slug} adopted={item.adopted} created={!entry.notCreated} onConfirm={onStop} />
      <PresetPreview item={item} unsaved={unsaved} update={update} canPreview={canPreview} />
      <LastRun slug={entry.slug} last={last} />
    </Stack>
  );
}
