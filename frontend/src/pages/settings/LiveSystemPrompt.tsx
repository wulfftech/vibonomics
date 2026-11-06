import { Alert, Code, Stack, Text } from '@mantine/core';
import { Disclosure } from './PresetParts';

/** Said wherever a preset's system prompt is shown, so the owner reads the same promise everywhere. */
export const SYSTEM_PROMPT_NOTE =
  'This preset has a system prompt. Vibonomics resends it unchanged on every update and backs up the old text before it replaces it.';

/**
 * A preset's live system prompt: what Vibonomics does with it, and the text itself behind a toggle. `prompt` is null
 * when OpenRouter gave none, which also happens when it says there is one but sent no text.
 */
export function LiveSystemPrompt({ present, prompt }: { present: boolean; prompt: string | null | undefined }) {
  if (!present) {
    return null;
  }
  return (
    <Alert color="blue" variant="light" py="xs">
      <Stack gap={4}>
        <Text size="sm">{SYSTEM_PROMPT_NOTE}</Text>
        {typeof prompt === 'string' && (
          <Disclosure
            label={`Show the system prompt (${prompt.length.toLocaleString('en')} ${prompt.length === 1 ? 'character' : 'characters'})`}
          >
            <Code block aria-label="Live system prompt" tabIndex={0} style={{ maxHeight: 320, overflow: 'auto' }}>
              {prompt}
            </Code>
          </Disclosure>
        )}
      </Stack>
    </Alert>
  );
}
