import { Alert, Button, Checkbox, Code, Group, Modal, Stack, Text } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useEffect, useState } from 'react';
import { useAdoptPreset, useInspectPreset } from '../../api/presets';
import { ApiError } from '../../api/client';
import type { PresetSettings } from '../../api/types';
import { ErrorState, LoadingState } from '../../components/DataState';
import { LiveSystemPrompt } from './LiveSystemPrompt';
import { PassthroughBlock } from './PresetParts';

/**
 * Adopting hands a preset made by hand to Vibonomics, so everything it would send back is shown first, as the raw
 * JSON, and has to be ticked as read. What is adopted is exactly what was shown: the answer is held here, and
 * inspecting again (after the preset changed on OpenRouter) clears the tick.
 */
function AdoptBody({
  slug,
  onClose,
  onAdopted,
}: {
  slug: string;
  onClose: () => void;
  onAdopted: (item: PresetSettings) => void;
}) {
  const inspect = useInspectPreset();
  const adopt = useAdoptPreset();
  const [read, setRead] = useState(false);
  const { mutate: inspectPreset } = inspect;

  useEffect(() => {
    inspectPreset(slug);
  }, [inspectPreset, slug]);

  const inspectAgain = () => {
    setRead(false);
    adopt.reset();
    inspectPreset(slug);
  };

  if (inspect.isPending || inspect.isIdle) {
    return <LoadingState label="Reading the preset from OpenRouter…" />;
  }
  if (inspect.isError) {
    return (
      <Stack gap="md">
        <ErrorState title="Could not read the preset" message={inspect.error.message} />
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Close
          </Button>
          <Button onClick={inspectAgain}>Inspect again</Button>
        </Group>
      </Stack>
    );
  }

  const seen = inspect.data;
  const blocked = seen.blockers.length > 0 || !seen.adoptable;
  const changed = adopt.error instanceof ApiError && adopt.error.status === 409;
  const confirm = () =>
    adopt.mutate(
      { slug, digest: seen.digest },
      {
        onSuccess: (item) => {
          onAdopted(item);
          onClose();
          notifications.show({
            message: `Adopted ${item.slug}. Writing is switched off until you turn it on.`,
            color: 'green',
          });
        },
      },
    );

  return (
    <Stack gap="md">
      <Text size="sm">
        Vibonomics would manage these keys of <Code>@preset/{seen.slug}</Code>:{' '}
        {seen.managed_keys.map((key, i) => (
          <span key={key}>
            {i > 0 && ', '}
            <Code>{key}</Code>
          </span>
        ))}
        . Adopting writes nothing: the preset is added to your list with writing switched off.
      </Text>
      <LiveSystemPrompt present={seen.has_system_prompt} prompt={seen.system_prompt} />
      <PassthroughBlock passthrough={seen.passthrough} />
      {seen.blockers.map((reason) => (
        <Alert key={reason} color="red" variant="light" py="xs">
          {reason}
        </Alert>
      ))}
      {blocked && seen.blockers.length === 0 && (
        <Alert color="red" variant="light" py="xs">
          This preset cannot be adopted.
        </Alert>
      )}
      <Checkbox
        label="I have read these keys"
        checked={read}
        disabled={blocked}
        onChange={(event) => setRead(event.currentTarget.checked)}
      />
      {adopt.isError && (
        <Alert color={changed ? 'yellow' : 'red'} variant="light" py="xs">
          <Stack gap="xs" align="flex-start">
            <Text size="sm">{adopt.error.message}</Text>
            {changed && (
              <Button size="compact-sm" variant="default" onClick={inspectAgain}>
                Inspect again
              </Button>
            )}
          </Stack>
        </Alert>
      )}
      <Group justify="flex-end">
        <Button variant="default" onClick={onClose}>
          Cancel
        </Button>
        <Button loading={adopt.isPending} disabled={blocked || !read} onClick={confirm}>
          Adopt this preset
        </Button>
      </Group>
    </Stack>
  );
}

/** Opens on a preset made by hand. The body mounts, and so inspects the preset anew, each time it opens. */
export function AdoptPresetModal({
  slug,
  opened,
  onClose,
  onAdopted,
}: {
  slug: string;
  opened: boolean;
  onClose: () => void;
  onAdopted: (item: PresetSettings) => void;
}) {
  return (
    <Modal opened={opened} onClose={onClose} title={`Adopt ${slug}`} size="lg" centered>
      <AdoptBody slug={slug} onClose={onClose} onAdopted={onAdopted} />
    </Modal>
  );
}
