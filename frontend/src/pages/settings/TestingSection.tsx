import {
  Alert,
  Badge,
  Card,
  Code,
  Group,
  NumberInput,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Text,
  Title,
} from '@mantine/core';
import { TriangleAlert } from 'lucide-react';
import { useMemo } from 'react';
import { useJudges } from '../../api/evals';
import type { FullSettings } from '../../api/types';
import { ErrorState, LoadingState } from '../../components/DataState';
import { judgeLabel, judgeValue, parseJudge } from '../../lib/judges';
import { SaveBar } from './SaveBar';
import { toNumber, useSettingsDraft } from './useSettingsDraft';

type SandboxMode = FullSettings['evals']['sandbox_mode'];

const SANDBOX_MODES: { value: SandboxMode; label: string }[] = [
  { value: 'auto', label: 'Auto' },
  { value: 'sidecar', label: 'Sidecar' },
  { value: 'local', label: 'Local' },
  { value: 'disabled', label: 'Disabled' },
];

function SandboxDescription({ mode }: { mode: SandboxMode }) {
  switch (mode) {
    case 'auto':
      return (
        <Text size="sm" c="dimmed">
          Uses the sidecar sandbox when <Code>VIBONOMICS_SANDBOX_URL</Code> is set, otherwise grading is judge-only.
        </Text>
      );
    case 'sidecar':
      return (
        <Text size="sm" c="dimmed">
          Runs model-written code in the separate sandbox container at <Code>VIBONOMICS_SANDBOX_URL</Code>.
        </Text>
      );
    case 'local':
      return (
        <Alert color="red" icon={<TriangleAlert size={16} />}>
          Runs model-written code inside the Vibonomics container itself, which can reach your stored keys and network.
          Only use this on trusted, isolated hosts.
        </Alert>
      );
    case 'disabled':
      return (
        <Text size="sm" c="dimmed">
          Code is never executed: coding tasks are graded by the judge from transcripts only.
        </Text>
      );
  }
}

export function TestingSection() {
  const { query, value, update, dirty, submit, saving, reset } = useSettingsDraft(['evals']);
  const judges = useJudges();
  const options = useMemo(
    () => (judges.data ?? []).map((j) => ({ value: judgeValue(j), label: judgeLabel(j) })),
    [judges.data],
  );

  if (query.isPending) {
    return <LoadingState />;
  }
  if (query.isError || value === null || query.data === undefined) {
    return <ErrorState message={query.error?.message} />;
  }
  const evals = value.evals;
  const setEvals = (patch: Partial<typeof evals>) => update((old) => ({ ...old, evals: { ...old.evals, ...patch } }));
  const current =
    evals.judge_provider_config_id != null && evals.judge_model_id
      ? judgeValue({ provider_id: evals.judge_provider_config_id, model_id: evals.judge_model_id })
      : null;
  const judgeOptions =
    current !== null && !options.some((o) => o.value === current)
      ? [{ value: current, label: `${evals.judge_model_id ?? ''} (unavailable)` }, ...options]
      : options;
  const effective = query.data.sandbox.effective_mode;

  return (
    <Stack gap="lg">
      <Stack gap={4}>
        <Title order={3}>Testing</Title>
        <Text size="sm" c="dimmed">
          Vibonomics&apos;s own tests run small coding and agentic tasks against models you pick, graded by a judge
          model. They make real API calls billed to your keys.
        </Text>
      </Stack>

      <Card withBorder padding="md">
        <Stack gap="md">
          <Title order={4}>Judge and limits</Title>
          <Select
            label="Default judge"
            description="Preselected when starting a test run. Pick a strong model with tool calling."
            placeholder={judges.isPending ? 'Loading…' : 'First available model'}
            data={judgeOptions}
            value={current}
            searchable
            clearable
            nothingFoundMessage="No matching model"
            maw={520}
            onChange={(next) => {
              const parsed = next ? parseJudge(next) : null;
              setEvals({
                judge_provider_config_id: parsed?.provider_id ?? null,
                judge_model_id: parsed?.model_id ?? null,
              });
            }}
          />
          <SimpleGrid cols={{ base: 1, sm: 2, md: 4 }} spacing="md">
            <NumberInput
              label="Max steps"
              description="Tool-call turns per task"
              min={2}
              max={60}
              value={evals.max_steps}
              onChange={(next) => setEvals({ max_steps: Math.round(toNumber(next, 14)) })}
            />
            <NumberInput
              label="Max output tokens"
              description="Per model response"
              min={256}
              max={65536}
              step={256}
              thousandSeparator=","
              value={evals.max_output_tokens}
              onChange={(next) => setEvals({ max_output_tokens: Math.round(toNumber(next, 4096)) })}
            />
            <NumberInput
              label="Concurrency"
              description="Tasks run in parallel"
              min={1}
              max={8}
              value={evals.concurrency}
              onChange={(next) => setEvals({ concurrency: Math.round(toNumber(next, 2)) })}
            />
            <NumberInput
              label="Request timeout"
              description="Per API call"
              min={10}
              max={1800}
              suffix=" s"
              value={evals.request_timeout_seconds}
              onChange={(next) => setEvals({ request_timeout_seconds: Math.round(toNumber(next, 180)) })}
            />
          </SimpleGrid>
        </Stack>
      </Card>

      <Card withBorder padding="md">
        <Stack gap="sm">
          <Group justify="space-between">
            <Title order={4}>Code sandbox</Title>
            <Group gap={6}>
              <Text size="sm" c="dimmed">
                Effective mode
              </Text>
              <Badge
                variant="light"
                color={effective === 'disabled' ? 'yellow' : effective === 'local' ? 'red' : 'green'}
              >
                {effective}
              </Badge>
            </Group>
          </Group>
          <SegmentedControl
            data={SANDBOX_MODES}
            value={evals.sandbox_mode}
            onChange={(next) => setEvals({ sandbox_mode: next })}
            style={{ alignSelf: 'flex-start' }}
          />
          <SandboxDescription mode={evals.sandbox_mode} />
          {evals.sandbox_mode === 'sidecar' && !query.data.sandbox.sidecar_url_set && (
            <Alert color="yellow" icon={<TriangleAlert size={16} />}>
              <Code>VIBONOMICS_SANDBOX_URL</Code> isn&apos;t set, so tests fall back to judge-only grading.
            </Alert>
          )}
          <Text size="xs" c="dimmed">
            The effective mode reflects the saved setting.
          </Text>
        </Stack>
      </Card>

      <SaveBar dirty={dirty} saving={saving} onReset={reset} onSave={() => submit()} />
    </Stack>
  );
}
