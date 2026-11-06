import { Alert, Button, Card, Group, Stack, Text, Title } from '@mantine/core';
import type { PresetSettings, PresetSlugState } from '../../api/types';
import { ErrorState } from '../../components/DataState';
import { dateTime, relativeTime } from '../../lib/format';
import { itemSlug } from '../../lib/presets';
import { PresetPlanView } from './PresetPlanView';
import { isWritable, plannedSlug, type PresetUpdate } from './usePresetUpdate';

/**
 * The dry run and the write for one preset. A plan is for the preset it was made for; one made for another preset
 * (the page keeps one at a time) is not shown here.
 */
export function PresetPreview({
  item,
  unsaved,
  update,
}: {
  item: PresetSettings;
  /** The settings differ from what is saved, or are not saved at all: they can be previewed, not applied. */
  unsaved: boolean;
  update: PresetUpdate;
}) {
  const { plan, apply } = update;
  const slug = itemSlug(item);
  const asked = plannedSlug(plan.variables) === slug;
  const current = plan.data?.slug === slug ? plan.data : undefined;
  // Apply plans from what is saved, so only a plan made from saved settings can be applied.
  const canApply = current !== undefined && isWritable(current) && item.enabled && !unsaved;

  return (
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
            <Button variant="default" loading={plan.isPending && asked} onClick={() => update.preview(item, unsaved)}>
              Preview
            </Button>
            <Button loading={apply.isPending} disabled={!canApply} onClick={update.applyPlan}>
              Apply this plan
            </Button>
          </Group>
        </Group>
        {unsaved && (
          <Alert color="blue" variant="light" py="xs">
            You have unsaved changes. Preview tries them out without saving them; save them to be able to apply.
          </Alert>
        )}
        {!unsaved && current !== undefined && !item.enabled && ['create', 'update'].includes(current.action) && (
          <Alert color="blue" variant="light" py="xs">
            Turn on &ldquo;Let Vibonomics write this preset&rdquo; and save to be able to apply it.
          </Alert>
        )}
        {!unsaved && current?.draft === true && (
          <Alert color="blue" variant="light" py="xs">
            This preview was made from settings that were not saved. Preview again to be able to apply.
          </Alert>
        )}
        {plan.isError && asked && <ErrorState message={plan.error.message} />}
        {current !== undefined && <PresetPlanView plan={current} />}
      </Stack>
    </Card>
  );
}

/** What this preset's last run did, from the local record only. Nothing before it has run. */
export function LastRun({ slug, last }: { slug: string; last: PresetSlugState | undefined }) {
  if (last?.last_run_at === null || last?.last_run_at === undefined) {
    return null;
  }
  return (
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
            Last written: {slug} v{last.version} on {last.model_id}, {relativeTime(last.applied_at)}.
          </Text>
        )}
      </Stack>
    </Card>
  );
}
