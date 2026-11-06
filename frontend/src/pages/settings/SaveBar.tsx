import { Button, Group } from '@mantine/core';
import { LeaveGuard } from './LeaveGuard';

/**
 * The footer every settings section shares: Discard (when edited) and Save.
 * `dirty` means the draft differs from what is saved. `canSave` is separate so that a draft that cannot be saved
 * yet (a blank name, say) still offers Discard, and still warns before it is left.
 */
export function SaveBar({
  dirty,
  canSave = true,
  saving,
  onSave,
  onReset,
}: {
  dirty: boolean;
  canSave?: boolean;
  saving: boolean;
  onSave: () => void;
  onReset: () => void;
}) {
  return (
    <>
      <LeaveGuard when={dirty} />
      <Group justify="flex-end">
        {dirty && (
          <Button variant="default" onClick={onReset} disabled={saving}>
            Discard changes
          </Button>
        )}
        <Button onClick={onSave} loading={saving} disabled={!dirty || !canSave}>
          Save
        </Button>
      </Group>
    </>
  );
}
