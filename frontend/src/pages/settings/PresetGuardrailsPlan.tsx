import { Box, Code, Group, SimpleGrid, Stack, Table, Text } from '@mantine/core';
import { CircleCheck, CircleQuestionMark, CircleX, type LucideIcon } from 'lucide-react';
import type { PresetPlan } from '../../api/types';
import { DASH, dateTime, relativeTime } from '../../lib/format';
import { describeLive } from '../../lib/presets';
import { tone } from '../../lib/tone';
import { Disclosure, ModelLink } from './PresetParts';

type Support = PresetPlan['capabilities'][number]['supports'][string];

/** The words lead every cell, so the colour and the icon are never the only thing that says what it is. */
const SUPPORT: Record<Support, { word: string; color: string; Icon: LucideIcon }> = {
  yes: { word: 'Supported', color: 'green', Icon: CircleCheck },
  no: { word: 'Not supported: ignored', color: 'red', Icon: CircleX },
  unknown: { word: 'Unknown', color: 'gray', Icon: CircleQuestionMark },
};

const count = (n: number) => `${n.toLocaleString('en')} ${n === 1 ? 'character' : 'characters'}`;

/** A guardrail's value on one line. The prompt is shown in its own block, so its row says only how long it is. */
function valueText(key: string, value: unknown): string {
  return key === 'system_prompt' && typeof value === 'string'
    ? `${count(value.length)}, shown below`
    : describeLive(value);
}

/** The guardrails this plan writes, and the ones it takes off. Nothing when it touches none. */
function GuardrailsWritten({ guardrails }: { guardrails: PresetPlan['guardrails'] }) {
  const written = Object.entries(guardrails.set);
  if (written.length === 0 && guardrails.cleared.length === 0) {
    return null;
  }
  return (
    <Stack gap={4}>
      <Text fw={600} size="sm">
        Guardrails this update writes
      </Text>
      <Box
        component="ul"
        m={0}
        p={0}
        style={{ listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 2 }}
        aria-label="Guardrails this update writes"
      >
        {written.map(([key, value]) => (
          <Box component="li" key={key}>
            <Text size="sm" style={{ overflowWrap: 'anywhere' }}>
              <Code>{key}</Code>: {valueText(key, value)}
            </Text>
          </Box>
        ))}
        {guardrails.cleared.map((key) => (
          <Box component="li" key={key}>
            <Text size="sm">
              removed: <Code>{key}</Code>
            </Text>
          </Box>
        ))}
      </Box>
    </Stack>
  );
}

function PromptText({ title, text }: { title: string; text: string | null }) {
  return (
    <Stack gap={2}>
      <Text size="xs" c="dimmed">
        {title}
      </Text>
      {text === null ? (
        <Text size="sm">None</Text>
      ) : (
        <Disclosure label={`Show ${title.toLowerCase()} prompt (${count(text.length)})`}>
          <Code block aria-label={`${title} system prompt`} tabIndex={0} style={{ maxHeight: 320, overflow: 'auto' }}>
            {text}
          </Code>
        </Disclosure>
      )}
    </Stack>
  );
}

/**
 * What the write does to the preset's system prompt. A write with no system message would erase it at OpenRouter, so
 * the live prompt is always sent back unless the owner manages it; this says which of those it is, and shows both texts
 * when they differ. A prompt that was replaced earlier says when it was backed up.
 */
function SystemPrompt({ plan }: { plan: PresetPlan }) {
  const { live, planned, source, changed } = plan.system_prompt;
  const backedUp = plan.system_backup_at;
  if (source === 'none' && backedUp === null) {
    return null;
  }
  return (
    <Stack gap={4}>
      <Text fw={600} size="sm">
        System prompt
      </Text>
      {source === 'kept' && <Text size="sm">The live prompt ({count(live?.length ?? 0)}) is resent unchanged.</Text>}
      {source === 'cleared' && <Text size="sm">The prompt will be removed.</Text>}
      {source === 'managed' && changed && (
        <SimpleGrid type="container" cols={{ base: 1, '36rem': 2 }} spacing="md">
          <PromptText title="Live" text={live} />
          <PromptText title="Planned" text={planned} />
        </SimpleGrid>
      )}
      {source === 'managed' && !changed && (
        <>
          <Text size="sm">The prompt Vibonomics manages is already on OpenRouter.</Text>
          <PromptText title="Planned" text={planned} />
        </>
      )}
      {backedUp !== null && (
        <Text size="sm" c="dimmed" title={dateTime(backedUp)}>
          The previous prompt was backed up {relativeTime(backedUp)}.
        </Text>
      )}
    </Stack>
  );
}

function SupportCell({ support }: { support: Support }) {
  const { word, color, Icon } = SUPPORT[support];
  return (
    <Group gap={6} wrap="nowrap">
      <Icon size={16} aria-hidden style={{ flexShrink: 0, color: tone(color) }} />
      <Text size="sm" style={{ color: tone(color) }}>
        {word}
      </Text>
    </Group>
  );
}

/**
 * Which endpoint takes which of the numeric guardrails the owner set, one row for each model the plan lists, since a
 * parameter an endpoint does not support is silently ignored there (and with Require parameters it refuses the call).
 */
function Capabilities({ plan }: { plan: PresetPlan }) {
  const rows = plan.capabilities;
  if (rows.length === 0) {
    return null;
  }
  const names = new Map(plan.picks.map((c) => [c.model_id, c.name]));
  const params = [...new Set(rows.flatMap((row) => Object.keys(row.supports)))];
  return (
    <Stack gap={4}>
      <Text fw={600} size="sm">
        Where the guardrails apply
      </Text>
      <Table.ScrollContainer minWidth={420}>
        <Table verticalSpacing={4} withRowBorders={false} aria-label="Where the guardrails apply">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Model</Table.Th>
              <Table.Th>Provider</Table.Th>
              {params.map((param) => (
                <Table.Th key={param}>{param}</Table.Th>
              ))}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((row) => (
              <Table.Tr key={`${row.model_id}-${row.provider ?? DASH}`}>
                <Table.Td>
                  <ModelLink id={row.model_id} name={names.get(row.model_id)} />
                </Table.Td>
                <Table.Td>
                  {row.provider === null ? (
                    <Text size="sm" c="dimmed">
                      no pin
                    </Text>
                  ) : (
                    <Code>{row.provider}</Code>
                  )}
                </Table.Td>
                {params.map((param) => (
                  <Table.Td key={param}>
                    <SupportCell support={row.supports[param] ?? 'unknown'} />
                  </Table.Td>
                ))}
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Stack>
  );
}

/** What a plan says about the guardrails: what it writes, the system prompt, and where each parameter applies. */
export function PlanGuardrails({ plan }: { plan: PresetPlan }) {
  return (
    <>
      <GuardrailsWritten guardrails={plan.guardrails} />
      <Capabilities plan={plan} />
      <SystemPrompt plan={plan} />
    </>
  );
}
