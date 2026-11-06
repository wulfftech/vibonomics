import { Button, Group, Modal, Stack, Text, TextInput } from '@mantine/core';
import { useState, type FormEvent } from 'react';
import { newNameProblem, PRESET_PREFIX, presetSlug } from '../../lib/presets';
import { TemplatePicker, type TemplateSource } from './TemplatePicker';

function NewPresetBody({
  templates,
  taken,
  onAccount,
  onCreate,
  onClose,
}: {
  templates: TemplateSource;
  taken: ReadonlySet<string>;
  onAccount: ReadonlySet<string>;
  onCreate: (name: string, template: string | null) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState('');
  const [template, setTemplate] = useState<string | null>(null);
  const slug = presetSlug(name);
  const problem = newNameProblem(name, taken, onAccount);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (slug !== '' && problem === null) {
      onCreate(name, template === '' ? null : template);
    }
  };
  return (
    <form onSubmit={submit}>
      <Stack gap="md">
        <TextInput
          label="Preset name"
          data-autofocus
          description={
            <>
              It becomes <code>@preset/{slug || '…'}</code>. The <code>{PRESET_PREFIX}</code> prefix is added for you:
              it is how Vibonomics knows a preset is its own, so one you made by hand is never touched. The name cannot
              be changed once the preset is added.
            </>
          }
          leftSection={
            <Text size="sm" c="dimmed">
              {PRESET_PREFIX}
            </Text>
          }
          leftSectionWidth={92}
          leftSectionPointerEvents="none"
          value={name}
          error={problem ?? undefined}
          onChange={(event) => setName(event.currentTarget.value)}
        />
        <TemplatePicker source={templates} value={template} onChange={setTemplate} />
        <Text size="sm" c="dimmed">
          Nothing is written to OpenRouter yet. Turn on writing for the preset and save to create it.
        </Text>
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={slug === '' || problem !== null}>
            Add preset
          </Button>
        </Group>
      </Stack>
    </form>
  );
}

/** Asks for the name of a new preset, and the preset to copy its tools and filters from. */
export function NewPresetModal({
  opened,
  onClose,
  ...rest
}: {
  opened: boolean;
  onClose: () => void;
  templates: TemplateSource;
  taken: ReadonlySet<string>;
  onAccount: ReadonlySet<string>;
  onCreate: (name: string, template: string | null) => void;
}) {
  return (
    <Modal opened={opened} onClose={onClose} title="New preset" size="lg" centered>
      <NewPresetBody {...rest} onClose={onClose} />
    </Modal>
  );
}
