import { Button, Group, Modal, Stack, Text } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';

/**
 * Takes a preset out of the list Vibonomics keeps up to date. It is a change to the settings like any other, so it
 * waits for Save, and Discard brings the preset back. Nothing happens on OpenRouter.
 */
export function StopManaging({
  slug,
  adopted,
  created,
  onConfirm,
}: {
  slug: string;
  adopted: boolean;
  /** The preset exists on OpenRouter; false for one that was added here and never saved. */
  created: boolean;
  onConfirm: () => void;
}) {
  const [opened, { open, close }] = useDisclosure(false);
  return (
    <>
      <Group justify="flex-start">
        <Button variant="light" color="red" onClick={open}>
          Stop managing
        </Button>
      </Group>
      <Modal opened={opened} onClose={close} title="Stop managing this preset?" centered>
        <Stack gap="sm">
          <Text size="sm">
            Vibonomics stops updating <code>{slug}</code> and takes it out of its list when you save.
            {created
              ? ' The preset stays on OpenRouter exactly as it is now; Vibonomics never deletes one.'
              : ' It was never saved, so nothing exists on OpenRouter to leave behind.'}
          </Text>
          {adopted && (
            <Text size="sm">
              You adopted this preset, so this also hands it back: Vibonomics will not write to it again unless you
              adopt it again.
            </Text>
          )}
        </Stack>
        <Group justify="flex-end" mt="lg">
          <Button variant="default" onClick={close}>
            Keep managing
          </Button>
          <Button
            color="red"
            onClick={() => {
              close();
              onConfirm();
            }}
          >
            Stop managing
          </Button>
        </Group>
      </Modal>
    </>
  );
}
