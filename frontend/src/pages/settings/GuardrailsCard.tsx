import { Alert, Box, Card, SimpleGrid, Stack, Text, Title } from '@mantine/core';
import type { GuardrailKey, PresetGuardrails } from '../../api/types';
import {
  asGuardrails,
  describeLive,
  draftOf,
  guardrailMode,
  guardrailProblems,
  idlePlaceholder,
  liveGuardrail,
  seedFromLive,
  setGuardrailMode,
  setGuardrailValue,
  type GuardrailMode,
  type GuardrailsDraft,
  type LivePreset,
  type NumberKey,
} from '../../lib/presets';
import { GuardrailRow } from './GuardrailRow';
import { IgnoreField, NumberField, PromptField, QuantizationsField, RequireField } from './GuardrailFields';

const INTRO =
  "Limits against runaway agents. Each setting is Left as it is on OpenRouter (not managed by Vibonomics), Set, or Removed. A client that sends its own temperature or max_tokens overrides the preset's value; repetition_penalty is rarely sent by clients, so it is the lever that works.";

const NUMBERS: { key: NumberKey; label: string; notes: string[] }[] = [
  {
    key: 'temperature',
    label: 'Temperature',
    notes: ["DeepSeek's own endpoint ignores temperature and the penalties in thinking mode."],
  },
  {
    key: 'max_tokens',
    label: 'Max tokens',
    notes: ['Reasoning tokens count toward this cap, so a low cap truncates thinking. It is stored as max_tokens.'],
  },
  {
    key: 'repetition_penalty',
    label: 'Repetition penalty',
    notes: ['1.0 is off; 1.05 to 1.15 is typical. Only some endpoints support it: Vibonomics pins ones that do.'],
  },
  { key: 'frequency_penalty', label: 'Frequency penalty', notes: [] },
  { key: 'presence_penalty', label: 'Presence penalty', notes: [] },
  { key: 'top_p', label: 'Top p', notes: [] },
];

const WIDE = { gridColumn: '1 / -1' } as const;
// The form sits in a column beside the list, so its columns follow the width of that column, not of the window.
const TWO_COLUMNS = { base: 1, '44rem': 2 };

/** The live system prompt in a few words: it is only text once a preview or an inspection has read it. */
function describePrompt(prompt: string | null | undefined): string {
  if (prompt === undefined) {
    return 'set (preview to read it)';
  }
  return prompt === null ? 'not set' : `${prompt.length.toLocaleString('en')} characters`;
}

/**
 * The settings that keep an agent in bounds, each left as it is on OpenRouter, set, or removed. Every one shows what
 * is on OpenRouter now, so a value can be chosen against it. A Set row without a usable value is flagged here and
 * turns Save and Preview off elsewhere; the draft is never sent in that state.
 */
export function GuardrailsCard({
  guardrails,
  live,
  onChange,
}: {
  guardrails: PresetGuardrails | undefined;
  live: LivePreset;
  onChange: (next: PresetGuardrails) => void;
}) {
  const g = draftOf(guardrails);
  const problems = guardrailProblems(g);
  const change = (next: GuardrailsDraft) => onChange(asGuardrails(next));

  const liveText = (key: GuardrailKey): string =>
    live.config === null
      ? 'not created yet'
      : key === 'system_prompt'
        ? describePrompt(live.systemPrompt)
        : describeLive(liveGuardrail(live.config, key));
  const seed = (key: GuardrailKey) =>
    key === 'system_prompt'
      ? typeof live.systemPrompt === 'string'
        ? live.systemPrompt
        : undefined
      : seedFromLive(key, liveGuardrail(live.config, key));
  // One row's props, which are the same for every kind of value.
  const row = (key: GuardrailKey, label: string, notes?: string[]) => {
    const mode = guardrailMode(g, key);
    return {
      row: {
        label,
        notes,
        mode,
        live: liveText(key),
        onMode: (next: GuardrailMode) => change(setGuardrailMode(g, key, next, seed(key))),
      },
      field: {
        label,
        disabled: mode !== 'set',
        placeholder: idlePlaceholder(mode),
        error: problems[key],
      },
    };
  };

  const quantizations = row('quantizations', 'Quantizations', [
    'A list leaves out endpoints with any other precision, including those OpenRouter reports as unknown, such as free and stealth models.',
  ]);
  const ignore = row('ignore', 'Ignored providers');
  const requireRow = row('require_parameters', 'Require parameters', [
    'OpenRouter then refuses a request when no endpoint supports every parameter; Vibonomics blocks an update that would cause that.',
  ]);
  const prompt = row('system_prompt', 'System prompt', [
    'Written as a system message on every update. Left alone, the live prompt is resent unchanged, because OpenRouter erases it otherwise. The previous prompt is backed up each time it is replaced.',
  ]);

  return (
    <Card withBorder padding="md">
      <Stack gap="md">
        <Stack gap={4}>
          <Title order={5}>Guardrails</Title>
          <Text size="sm" c="dimmed">
            {INTRO}
          </Text>
        </Stack>
        {Object.keys(problems).length > 0 && (
          <Alert color="red" variant="light" py="xs">
            Some settings below need a value or a different one. Save and Preview stay off until they are fixed, or set
            to Leave or Remove.
          </Alert>
        )}
        <SimpleGrid type="container" cols={TWO_COLUMNS} spacing="lg" verticalSpacing="lg">
          {NUMBERS.map(({ key, label, notes }) => {
            const { row: rowProps, field } = row(key, label, notes);
            return (
              <GuardrailRow key={key} {...rowProps}>
                <NumberField
                  name={key}
                  {...field}
                  value={g[key]}
                  onChange={(value) => change(setGuardrailValue(g, key, value))}
                />
              </GuardrailRow>
            );
          })}
          <GuardrailRow {...quantizations.row}>
            <QuantizationsField
              {...quantizations.field}
              value={g.quantizations}
              onChange={(value) => change(setGuardrailValue(g, 'quantizations', value))}
            />
          </GuardrailRow>
          <GuardrailRow {...ignore.row}>
            <IgnoreField
              {...ignore.field}
              value={g.ignore}
              onChange={(value) => change(setGuardrailValue(g, 'ignore', value))}
            />
          </GuardrailRow>
          <GuardrailRow {...requireRow.row}>
            <RequireField
              label="Only route to endpoints that support every parameter"
              disabled={requireRow.field.disabled}
              error={requireRow.field.error}
              value={g.require_parameters}
              onChange={(value) => change(setGuardrailValue(g, 'require_parameters', value))}
            />
          </GuardrailRow>
          <Box style={WIDE}>
            <GuardrailRow {...prompt.row}>
              <PromptField
                {...prompt.field}
                value={g.system_prompt}
                onChange={(value) => change(setGuardrailValue(g, 'system_prompt', value))}
              />
            </GuardrailRow>
          </Box>
        </SimpleGrid>
      </Stack>
    </Card>
  );
}
