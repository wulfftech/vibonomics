import { Alert, Box, Group, Stack, Text } from '@mantine/core';
import { Check, CircleAlert, TriangleAlert, type LucideIcon } from 'lucide-react';
import type { PresetPlan } from '../../api/types';
import { dateTime, relativeTime } from '../../lib/format';
import { tone } from '../../lib/tone';
import { Disclosure } from './PresetParts';

type Finding = PresetPlan['validation'][number];

/** The word leads every row, so the colour and the icon are never the only thing that says what it is. */
const LEVELS: Record<Finding['level'], { word: string; color: string; Icon: LucideIcon }> = {
  error: { word: 'Error', color: 'red', Icon: CircleAlert },
  warning: { word: 'Warning', color: 'yellow', Icon: TriangleAlert },
  ok: { word: 'Passed', color: 'green', Icon: Check },
};

function count(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** What the findings add up to, as the one line above them. */
function summary(errors: number, warnings: number, passed: number): string {
  const parts = [
    errors > 0 ? count(errors, 'error', 'errors') : null,
    warnings > 0 ? count(warnings, 'warning', 'warnings') : null,
    passed > 0 ? count(passed, 'check passed', 'checks passed') : null,
  ].filter((part) => part !== null);
  return parts.length === 0 ? 'Nothing needed checking.' : parts.join(', ');
}

/** The model and provider a finding is about, unless its message already names them. */
function subject(finding: Finding): string | null {
  const named = [finding.model, finding.provider].filter(
    (name): name is string => name !== null && name !== '' && !finding.message.includes(name),
  );
  return named.length === 0 ? null : named.join(' via ');
}

function FindingRow({ finding }: { finding: Finding }) {
  const { word, color, Icon } = LEVELS[finding.level];
  const about = subject(finding);
  return (
    <Box component="li">
      <Group gap={6} wrap="nowrap" align="flex-start">
        <Icon size={16} aria-hidden style={{ flexShrink: 0, marginTop: 2, color: tone(color) }} />
        <Text size="sm">
          <Text span fw={600} style={{ color: tone(color) }}>
            {word}:
          </Text>{' '}
          {finding.message}
          {about !== null && (
            <Text span c="dimmed">
              {' '}
              ({about})
            </Text>
          )}
        </Text>
      </Group>
    </Box>
  );
}

function FindingList({ findings, label }: { findings: Finding[]; label: string }) {
  return (
    <Box
      component="ul"
      m={0}
      p={0}
      style={{ listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 4 }}
      aria-label={label}
    >
      {findings.map((finding, i) => (
        <FindingRow key={`${finding.check}-${finding.model}-${finding.provider}-${i}`} finding={finding} />
      ))}
    </Box>
  );
}

/**
 * What OpenRouter's live listing said about this plan when it was made: the problems first, the checks that passed
 * folded away. Nothing for a plan that was not checked. An error also says the update will not be sent, which is the
 * reason Apply is off.
 */
export function PresetLiveCheck({ plan }: { plan: PresetPlan }) {
  const checked = plan.validated_at !== null;
  const errors = plan.validation.filter((f) => f.level === 'error');
  const warnings = plan.validation.filter((f) => f.level === 'warning');
  const passed = plan.validation.filter((f) => f.level === 'ok');
  if (!checked && plan.valid) {
    return null;
  }
  return (
    <Stack gap="xs">
      {!plan.valid && (
        <Alert color="red" variant="light" py="xs" icon={<CircleAlert size={16} />}>
          {plan.action === 'unchanged'
            ? 'The live check found a problem with what is on OpenRouter now.'
            : 'The live check found a problem, so this update will not be sent. Fix it or preview again to re-check.'}
        </Alert>
      )}
      {checked && (
        <Stack gap={4}>
          <Group gap="xs" align="baseline">
            <Text fw={600} size="sm">
              Checked against live providers
            </Text>
            <Text size="xs" c="dimmed" title={dateTime(plan.validated_at)}>
              {relativeTime(plan.validated_at)}
            </Text>
          </Group>
          <Text size="sm">{summary(errors.length, warnings.length, passed.length)}</Text>
          {errors.length + warnings.length > 0 && (
            <FindingList findings={[...errors, ...warnings]} label="Live check problems" />
          )}
          {passed.length > 0 && (
            <Disclosure label={`Passed checks (${passed.length})`}>
              <FindingList findings={passed} label="Passed checks" />
            </Disclosure>
          )}
        </Stack>
      )}
    </Stack>
  );
}
