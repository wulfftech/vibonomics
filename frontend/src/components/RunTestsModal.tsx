import {
  Alert,
  Anchor,
  Badge,
  Button,
  Checkbox,
  Group,
  Modal,
  Pill,
  ScrollArea,
  Select,
  Stack,
  Text,
} from '@mantine/core';
import { tone } from '../lib/tone';
import { useMediaQuery } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { Info, Play, TriangleAlert } from 'lucide-react';
import { useMemo, useState } from 'react';
import { Link } from 'react-router';
import { useJudges, useStartRun, useSuites } from '../api/evals';
import { useSettings } from '../api/settings';
import { useSystemStatus } from '../api/system';
import type { ModelRow } from '../api/types';
import { judgeLabel, judgeValue, parseJudge } from '../lib/judges';
import { ErrorState, LoadingState } from './DataState';

const MAX_TARGETS = 25;

const CATEGORY_COLORS: Record<string, string> = {
  coding: 'teal',
  agentic: 'grape',
  reasoning: 'blue',
  intelligence: 'blue',
  tools: 'orange',
};

/** Pick a suite and a judge, then start a test run against the selected models. */
export function RunTestsModal({
  opened,
  targets,
  onRemove,
  onClose,
  onStarted,
}: {
  opened: boolean;
  targets: ModelRow[];
  onRemove: (id: number) => void;
  onClose: () => void;
  onStarted: (runId: number) => void;
}) {
  const mobile = useMediaQuery('(max-width: 48em)');
  const suites = useSuites(opened);
  const judges = useJudges(opened);
  const settings = useSettings();
  const system = useSystemStatus();
  const start = useStartRun();

  const [suiteId, setSuiteId] = useState<string | null>(null);
  const [taskIds, setTaskIds] = useState<string[] | null>(null);
  const [judge, setJudge] = useState<string | null>(null);

  const suite = suites.data?.find((s) => s.id === suiteId) ?? suites.data?.[0];
  const selectedTasks = taskIds ?? suite?.tasks.map((task) => task.id) ?? [];

  const judgeOptions = useMemo(
    () => (judges.data ?? []).map((j) => ({ value: judgeValue(j), label: judgeLabel(j) })),
    [judges.data],
  );
  const defaultJudge = useMemo(() => {
    const evals = settings.data?.settings.evals;
    const configured =
      evals?.judge_provider_config_id != null && evals.judge_model_id
        ? judgeValue({ provider_id: evals.judge_provider_config_id, model_id: evals.judge_model_id })
        : null;
    if (configured && judgeOptions.some((option) => option.value === configured)) {
      return configured;
    }
    return judgeOptions[0]?.value ?? null;
  }, [settings.data, judgeOptions]);
  const judgeChoice = judge ?? defaultJudge;
  const selectedJudge = useMemo(
    () => (judges.data ?? []).find((j) => judgeValue(j) === judgeChoice) ?? null,
    [judges.data, judgeChoice],
  );

  const tooMany = targets.length > MAX_TARGETS;
  const sandboxDisabled = system.data?.sandbox_mode === 'disabled';

  const submit = () => {
    const parsed = judgeChoice ? parseJudge(judgeChoice) : null;
    if (!suite || !parsed) {
      return;
    }
    const all = selectedTasks.length === suite.tasks.length;
    start.mutate(
      {
        suite: suite.id,
        targets: targets.map((row) => ({ provider_id: row.provider_id, model_id: row.model_id })),
        judge: parsed,
        task_ids: all ? null : selectedTasks,
      },
      {
        onSuccess: (run) => {
          notifications.show({ message: `Test run #${run.id} started`, color: 'green' });
          onStarted(run.id);
        },
        onError: (error) => notifications.show({ message: error.message, color: 'red' }),
      },
    );
  };

  return (
    <Modal opened={opened} onClose={onClose} title="Test selected models" size="lg" fullScreen={mobile}>
      <Stack gap="md">
        <Stack gap={4}>
          <Text size="sm" fw={500}>
            Models ({targets.length})
          </Text>
          {targets.length === 0 ? (
            <Text size="sm" c="dimmed">
              No models selected.
            </Text>
          ) : (
            <ScrollArea.Autosize mah={120}>
              <Pill.Group>
                {targets.map((row) => (
                  <Pill key={row.id} withRemoveButton onRemove={() => onRemove(row.id)}>
                    {row.name} · {row.provider_name}
                  </Pill>
                ))}
              </Pill.Group>
            </ScrollArea.Autosize>
          )}
          {tooMany && (
            <Text size="sm" style={{ color: tone('red') }}>
              A run can test at most {MAX_TARGETS} models.
            </Text>
          )}
        </Stack>

        {suites.isPending && opened && <LoadingState label="Loading suites…" />}
        {suites.isError && <ErrorState message={suites.error.message} />}
        {suites.data && suites.data.length === 0 && (
          <Alert color="yellow" icon={<TriangleAlert size={16} />}>
            No test suites are available in this build yet.
          </Alert>
        )}
        {suite && (
          <Stack gap="xs">
            <Select
              label="Suite"
              data={(suites.data ?? []).map((s) => ({ value: s.id, label: s.name }))}
              value={suite.id}
              allowDeselect={false}
              onChange={(value) => {
                setSuiteId(value);
                setTaskIds(null);
              }}
            />
            <Text size="sm" c="dimmed">
              {suite.description}
            </Text>
            <Checkbox.Group
              label={`Tasks (${selectedTasks.length} of ${suite.tasks.length})`}
              value={selectedTasks}
              onChange={setTaskIds}
            >
              <ScrollArea.Autosize mah={220} mt={4}>
                <Stack gap={6}>
                  {suite.tasks.map((task) => (
                    <Checkbox
                      key={task.id}
                      value={task.id}
                      label={
                        <Group gap={6} wrap="nowrap">
                          <Text size="sm">{task.name}</Text>
                          <Badge size="xs" variant="light" color={CATEGORY_COLORS[task.category] ?? 'gray'}>
                            {task.category}
                          </Badge>
                          {task.uses_sandbox && (
                            <Badge size="xs" variant="light" color="gray">
                              runs code
                            </Badge>
                          )}
                        </Group>
                      }
                      description={task.description}
                    />
                  ))}
                </Stack>
              </ScrollArea.Autosize>
            </Checkbox.Group>
          </Stack>
        )}

        <Select
          label="Judge"
          description="Administers the agentic tasks and grades the answers. Pick a strong model."
          placeholder={judges.isPending ? 'Loading…' : 'No usable models — add an API key first'}
          data={judgeOptions}
          value={judgeChoice}
          searchable
          allowDeselect={false}
          nothingFoundMessage="No matching model"
          onChange={setJudge}
          error={judges.error?.message}
        />

        {selectedJudge?.note && (
          <Alert color="yellow" icon={<TriangleAlert size={16} />}>
            {selectedJudge.note} Pick a paid model unless you only want the deterministic checks.
          </Alert>
        )}

        {sandboxDisabled && (
          <Alert color="yellow" icon={<TriangleAlert size={16} />}>
            The code sandbox is disabled, so code isn&apos;t executed: coding tasks are judged from transcripts only.{' '}
            <Anchor component={Link} to="/settings/testing" size="sm" onClick={onClose}>
              Settings › Testing
            </Anchor>
          </Alert>
        )}

        <Alert color="blue" icon={<Info size={16} />}>
          Tests make real API calls billed to your provider keys. Free models cost nothing.
        </Alert>

        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button
            leftSection={<Play size={16} />}
            loading={start.isPending}
            disabled={targets.length === 0 || tooMany || !suite || !judgeChoice || selectedTasks.length === 0}
            onClick={submit}
          >
            Start
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
