import { Button, Group } from '@mantine/core';

/** The footer every settings section shares: Discard (when edited) and Save. */
export function SaveBar({
  dirty,
  saving,
  onSave,
  onReset,
}: {
  dirty: boolean;
  saving: boolean;
  onSave: () => void;
  onReset: () => void;
}) {
  return (
    <Group justify="flex-end">
      {dirty && (
        <Button variant="default" onClick={onReset} disabled={saving}>
          Discard changes
        </Button>
      )}
      <Button onClick={onSave} loading={saving} disabled={!dirty}>
        Save
      </Button>
    </Group>
  );
}
