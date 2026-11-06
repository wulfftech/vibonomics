import { notifications } from '@mantine/notifications';
import { useApplyPreset, usePresetPlan, useSyncPresets } from '../../api/presets';
import type { PresetPlan, PresetSettings } from '../../api/types';
import { itemSlug } from '../../lib/presets';

/** The slug a plan request was made for. */
export function plannedSlug(request: { slug: string } | { settings: PresetSettings } | undefined): string | null {
  if (request === undefined) {
    return null;
  }
  return 'slug' in request ? request.slug : itemSlug(request.settings);
}

/** A plan that can be sent: it writes something, and it was made from saved settings. */
export function isWritable(plan: PresetPlan): boolean {
  return ['create', 'update'].includes(plan.action) && Boolean(plan.digest) && !plan.draft;
}

/**
 * The dry run and the write for whichever preset is open. One plan is kept at a time, and it says whose it is
 * (`plan.slug`), so a page that shows another preset must not show it.
 */
export function usePresetUpdate() {
  const plan = usePresetPlan();
  const apply = useApplyPreset();
  const sync = useSyncPresets();

  // Planned again afterwards, so the page shows what is live now rather than the plan that was just sent.
  const send = (slug: string, digest: string) => {
    apply.mutate(
      { slug, digest },
      {
        onSuccess: (result) => {
          notifications.show({
            message: result.message,
            color: result.unsaved.length > 0 ? 'yellow' : result.applied ? 'green' : 'blue',
            autoClose: result.unsaved.length > 0 ? 12000 : 5000,
          });
          plan.mutate({ slug });
          // The catalogue is only read from OpenRouter on its timer, so a preset just created would stay "not created
          // yet" for half an hour, and an update would show the old version.
          if (result.applied) {
            sync.mutate();
          }
        },
        onError: (error) => {
          notifications.show({ message: error.message, color: 'red' });
          plan.mutate({ slug });
        },
      },
    );
  };

  return {
    plan,
    apply,
    /** Unsaved edits are previewed as they stand, so they can be tried before they are kept. */
    preview: (item: PresetSettings, unsaved: boolean) =>
      plan.mutate(unsaved ? { settings: item } : { slug: itemSlug(item) }),
    /** Apply the plan on screen, which `isWritable` has to have allowed. */
    applyPlan: () => {
      const current = plan.data;
      if (current?.digest) {
        send(current.slug, current.digest);
      }
    },
    /**
     * Saving with writing allowed sends the update right away, so a change reaches OpenRouter without a second
     * step. It plans from what was just saved, and sends nothing when that plan has nothing to write.
     */
    pushSaved: (slug: string) =>
      plan.mutate(
        { slug },
        {
          onSuccess: (fresh) => {
            if (fresh.draft) {
              return;
            }
            if (!isWritable(fresh) || !fresh.digest) {
              notifications.show({ message: fresh.reason, color: fresh.action === 'blocked' ? 'yellow' : 'blue' });
              return;
            }
            send(fresh.slug, fresh.digest);
          },
          onError: (error) => notifications.show({ message: error.message, color: 'red' }),
        },
      ),
  };
}

export type PresetUpdate = ReturnType<typeof usePresetUpdate>;
