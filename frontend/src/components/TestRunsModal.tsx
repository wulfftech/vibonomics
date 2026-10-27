import {
  Alert,
  Badge,
  Button,
  Card,
  Grid,
  Group,
  Modal,
  Popover,
  Progress,
  ScrollArea,
  SimpleGrid,
  Spoiler,
  Stack,
  Table,
  Text,
  Title,
  Tooltip,
  UnstyledButton,
} from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { ChevronDown, ChevronRight, CircleAlert, Info, Square, Trash } from 'lucide-react';
import { Fragment, useMemo, useState } from 'react';
import { isActiveRun, useCancelRun, useDeleteRun, useJudges, useRun, useRuns, useSuites } from '../api/evals';
import type { EvalResultOut, EvalRunOut } from '../api/types';
import { compactCount, dateTime, DASH, duration, integer, money, relativeTime } from '../lib/format';
import { isGraded, statusColor, statusLabel } from '../lib/status';
import { ScoreNumber } from './models/cells';
import { EmptyState, ErrorState, LoadingState } from './DataState';
import { TranscriptViewer } from './TranscriptViewer';

type Json = Record<string, unknown>;

function isObject(value: unknown): value is Json {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function progressOf(run: EvalRunOut): { done: number; total: number; current: string | null; note: string | null } {
  const p = run.progress;
  return {
    done: num(p.done) ?? 0,
    total: num(p.total) ?? 0,
    current: typeof p.current === 'string' ? p.current : null,
    note: typeof p.note === 'string' ? p.note : null,
  };
}

function RunListItem({
  run,
  suiteName,
  active,
  onSelect,
}: {
  run: EvalRunOut;
  suiteName: string;
  active: boolean;
  onSelect: () => void;
}) {
  const { done, total } = progressOf(run);
  return (
    <UnstyledButton onClick={onSelect} aria-current={active} w="100%">
      <Card withBorder padding="xs" style={active ? { borderColor: 'var(--mantine-primary-color-filled)' } : undefined}>
        <Stack gap={2}>
          <Group justify="space-between" wrap="nowrap">
            <Text size="sm" fw={500} truncate="end">
              #{run.id} · {suiteName}
            </Text>
            <Badge size="xs" variant="light" color={statusColor(run.status)}>
              {statusLabel(run.status)}
            </Badge>
          </Group>
          <Text size="xs" c="dimmed">
            {relativeTime(run.created_at)} · {run.targets.length} model{run.targets.length === 1 ? '' : 's'} · {done}/
            {total}
          </Text>
        </Stack>
      </Card>
    </UnstyledButton>
  );
}

function TargetSummaryCard({ name, provider, summary }: { name: string; provider: string; summary: Json | null }) {
  const score = summary ? num(summary.score) : null;
  const passed = summary ? num(summary.passed) : null;
  const tasks = summary ? num(summary.tasks) : null;
  const errored = summary ? (num(summary.errors) ?? 0) : 0;
  const ungraded = summary ? (num(summary.ungraded) ?? 0) : 0;
  const cost = summary ? num(summary.cost ?? summary.cost_usd) : null;
  const tokens = summary ? num(summary.tokens) : null;
  const assessment = summary && typeof summary.assessment === 'string' ? summary.assessment : null;
  return (
    <Card withBorder padding="sm">
      <Stack gap={4}>
        <Group justify="space-between" wrap="nowrap" align="flex-start">
          <Stack gap={0} miw={0}>
            <Text size="sm" fw={500} truncate="end">
              {name}
            </Text>
            <Text size="xs" c="dimmed">
              {provider}
            </Text>
          </Stack>
          <Text size="xl" lh={1}>
            <ScoreNumber value={score} bold />
          </Text>
        </Group>
        <Text size="xs" c="dimmed">
          {passed !== null && tasks !== null ? `${passed}/${tasks} passed` : DASH} · {money(cost)} ·{' '}
          {tokens !== null ? `${compactCount(tokens)} tokens` : DASH}
        </Text>
        {(errored > 0 || ungraded > 0) && (
          <Tooltip
            multiline
            maw={320}
            withArrow
            label="These tasks have no grade, so they are left out of the score rather than averaged in as zero. Expand a row to see why."
          >
            <Text size="xs" c="dimmed" style={{ cursor: 'help' }}>
              {[errored > 0 ? `${errored} errored` : null, ungraded > 0 ? `${ungraded} ungraded` : null]
                .filter(Boolean)
                .join(' · ')}{' '}
              — not scored
            </Text>
          </Tooltip>
        )}
        {assessment && (
          <Spoiler maxHeight={60} showLabel="More" hideLabel="Less">
            <Text size="xs" style={{ whiteSpace: 'pre-wrap' }}>
              {assessment}
            </Text>
          </Spoiler>
        )}
      </Stack>
    </Card>
  );
}

function ResultRow({
  result,
  modelName,
  showModel,
  expanded,
  onToggle,
  columns,
}: {
  result: EvalResultOut;
  modelName: string;
  showModel: boolean;
  expanded: boolean;
  onToggle: () => void;
  columns: number;
}) {
  return (
    <Fragment>
      <Table.Tr style={{ cursor: 'pointer' }} onClick={onToggle}>
        <Table.Td>
          <Group gap={4} wrap="nowrap">
            {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            <Text size="sm">{result.task_id}</Text>
          </Group>
        </Table.Td>
        {showModel && (
          <Table.Td>
            <Text size="xs" truncate="end">
              {modelName}
            </Text>
          </Table.Td>
        )}
        <Table.Td>
          <Text size="xs">{result.category}</Text>
        </Table.Td>
        <Table.Td>
          <Badge size="xs" variant="light" color={statusColor(result.status)}>
            {statusLabel(result.status)}
          </Badge>
        </Table.Td>
        <Table.Td>
          {/* The API stores 0.0 for error/ungraded rows; a dash is honest, a zero is not. */}
          <ScoreNumber value={isGraded(result.status) ? result.score : null} bold />
        </Table.Td>
        <Table.Td>
          <ScoreNumber value={result.check_score} />
        </Table.Td>
        <Table.Td>
          <ScoreNumber value={result.judge_score} />
        </Table.Td>
        <Table.Td>{result.steps}</Table.Td>
        <Table.Td>
          <Text size="xs">{compactCount(result.input_tokens + result.output_tokens)}</Text>
        </Table.Td>
        <Table.Td>
          <Text size="xs">{money(result.cost_usd)}</Text>
        </Table.Td>
        <Table.Td>
          <Text size="xs">{duration(result.latency_ms)}</Text>
        </Table.Td>
      </Table.Tr>
      {expanded && (
        <Table.Tr>
          <Table.Td colSpan={columns} bg="var(--mantine-color-default-hover)">
            <Stack gap="sm" py="xs">
              {result.judge_notes && (
                <Stack gap={2}>
                  <Text size="xs" fw={600}>
                    Judge notes
                  </Text>
                  <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
                    {result.judge_notes}
                  </Text>
                </Stack>
              )}
              {result.error && (
                <Alert color="red" icon={<CircleAlert size={16} />} p="xs">
                  <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
                    {result.error}
                  </Text>
                </Alert>
              )}
              <Text size="xs" c="dimmed">
                {integer(result.input_tokens)} input + {integer(result.output_tokens)} output tokens
              </Text>
              <Text size="xs" fw={600}>
                Transcript
              </Text>
              <ScrollArea.Autosize mah={480}>
                <TranscriptViewer transcript={result.transcript} />
              </ScrollArea.Autosize>
            </Stack>
          </Table.Td>
        </Table.Tr>
      )}
    </Fragment>
  );
}

function DeleteRunButton({ runId, onDeleted }: { runId: number; onDeleted: () => void }) {
  const remove = useDeleteRun();
  const [opened, setOpened] = useState(false);
  return (
    <Popover opened={opened} onChange={setOpened} position="bottom-end" withArrow>
      <Popover.Target>
        <Button
          variant="light"
          color="red"
          size="xs"
          leftSection={<Trash size={14} />}
          onClick={() => setOpened((o) => !o)}
        >
          Delete
        </Button>
      </Popover.Target>
      <Popover.Dropdown>
        <Stack gap="xs">
          <Text size="sm">Delete run #{runId} and its results?</Text>
          <Group justify="flex-end" gap="xs">
            <Button variant="default" size="xs" onClick={() => setOpened(false)}>
              Cancel
            </Button>
            <Button
              color="red"
              size="xs"
              loading={remove.isPending}
              onClick={() =>
                remove.mutate(runId, {
                  onSuccess: () => {
                    setOpened(false);
                    notifications.show({ message: `Run #${runId} deleted`, color: 'green' });
                    onDeleted();
                  },
                  onError: (error) => notifications.show({ message: error.message, color: 'red' }),
                })
              }
            >
              Delete
            </Button>
          </Group>
        </Stack>
      </Popover.Dropdown>
    </Popover>
  );
}

function RunDetail({
  runId,
  suiteName,
  onDeleted,
}: {
  runId: number;
  suiteName: (id: string) => string;
  onDeleted: () => void;
}) {
  const detail = useRun(runId);
  const judges = useJudges();
  const cancel = useCancelRun();
  const [expanded, setExpanded] = useState<ReadonlySet<number>>(new Set());

  if (detail.isPending) {
    return <LoadingState />;
  }
  if (detail.isError) {
    return <ErrorState message={detail.error.message} />;
  }
  const { run, results } = detail.data;
  const { done, total, current, note } = progressOf(run);
  const active = isActiveRun(run.status);
  const judge = judges.data?.find((j) => j.provider_id === run.judge_provider_id && j.model_id === run.judge_model_id);
  const summaries = isObject(run.summary.targets) ? run.summary.targets : {};
  const judgeError = typeof run.summary.judge_error === 'string' ? run.summary.judge_error : null;
  const summaryNote = typeof run.summary.note === 'string' ? run.summary.note : null;
  const names = new Map(run.targets.map((t) => [`${t.provider_id}|${t.model_id}`, `${t.name} · ${t.provider_name}`]));
  const showModel = run.targets.length > 1;
  const columns = showModel ? 11 : 10;

  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-start">
        <Stack gap={2}>
          <Group gap="xs">
            <Title order={4}>
              Run #{run.id} · {suiteName(run.suite)}
            </Title>
            <Badge variant="light" color={statusColor(run.status)}>
              {statusLabel(run.status)}
            </Badge>
          </Group>
          <Text size="xs" c="dimmed">
            Started {dateTime(run.created_at)}
            {run.finished_at ? ` · finished ${dateTime(run.finished_at)}` : ''}
          </Text>
          <Text size="xs" c="dimmed">
            Judge: {judge ? `${judge.name} — ${judge.provider_name}` : (run.judge_model_id ?? DASH)}
          </Text>
        </Stack>
        <Group gap="xs">
          {active && (
            <Button
              variant="default"
              size="xs"
              leftSection={<Square size={14} />}
              loading={cancel.isPending}
              onClick={() =>
                cancel.mutate(run.id, {
                  onError: (error) => notifications.show({ message: error.message, color: 'red' }),
                })
              }
            >
              Cancel run
            </Button>
          )}
          {!active && <DeleteRunButton runId={run.id} onDeleted={onDeleted} />}
        </Group>
      </Group>

      {active && (
        <Stack gap={4}>
          <Progress value={total > 0 ? (done / total) * 100 : 0} striped animated aria-label="Run progress" />
          <Text size="xs" c="dimmed">
            {done}/{total} tasks{current ? ` · ${current}` : ''}
          </Text>
        </Stack>
      )}
      {note && (
        <Text size="sm" c="dimmed">
          {note}
        </Text>
      )}
      {run.error && <ErrorState title="The run failed" message={run.error} />}
      {judgeError && (
        <Alert color="yellow" icon={<CircleAlert size={16} />} title="The judge model failed">
          {judgeError}
          <br />
          Scores below use deterministic checks only. Choose a different judge and run again for quality grading and the
          written assessment.
        </Alert>
      )}
      {summaryNote && !judgeError && (
        <Alert color="blue" icon={<Info size={16} />} title="About these scores">
          {summaryNote}
        </Alert>
      )}
      {summaryNote && judgeError && (
        <Text size="xs" c="dimmed">
          {summaryNote}
        </Text>
      )}

      <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }} spacing="sm">
        {run.targets.map((target) => {
          const key = `${target.provider_id}|${target.model_id}`;
          const summary = summaries[key];
          return (
            <TargetSummaryCard
              key={key}
              name={target.name}
              provider={target.provider_name}
              summary={isObject(summary) ? summary : null}
            />
          );
        })}
      </SimpleGrid>

      {results.length === 0 ? (
        <EmptyState message={active ? 'No results yet.' : 'This run has no results.'} />
      ) : (
        <Card withBorder padding="md">
          <Table.ScrollContainer minWidth={760}>
            <Table verticalSpacing="xs">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Task</Table.Th>
                  {showModel && <Table.Th>Model</Table.Th>}
                  <Table.Th>Category</Table.Th>
                  <Table.Th>Status</Table.Th>
                  <Table.Th>Score</Table.Th>
                  <Table.Th>Check</Table.Th>
                  <Table.Th>Judge</Table.Th>
                  <Table.Th>Steps</Table.Th>
                  <Table.Th>Tokens</Table.Th>
                  <Table.Th>Cost</Table.Th>
                  <Table.Th>Latency</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {results.map((result) => (
                  <ResultRow
                    key={result.id}
                    result={result}
                    modelName={names.get(`${result.provider_id}|${result.model_id}`) ?? result.model_id}
                    showModel={showModel}
                    columns={columns}
                    expanded={expanded.has(result.id)}
                    onToggle={() =>
                      setExpanded((old) => {
                        const next = new Set(old);
                        if (next.has(result.id)) {
                          next.delete(result.id);
                        } else {
                          next.add(result.id);
                        }
                        return next;
                      })
                    }
                  />
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </Card>
      )}
    </Stack>
  );
}

function RunsBody({ initialRunId }: { initialRunId: number | null }) {
  const runs = useRuns();
  const suites = useSuites();
  const [selected, setSelected] = useState<number | null>(initialRunId);
  const suiteNames = useMemo(() => new Map((suites.data ?? []).map((s) => [s.id, s.name])), [suites.data]);
  const suiteName = (id: string) => suiteNames.get(id) ?? id;

  if (runs.isPending) {
    return <LoadingState />;
  }
  if (runs.isError) {
    return <ErrorState message={runs.error.message} />;
  }
  if (runs.data.length === 0 && selected === null) {
    return <EmptyState message="No test runs yet. Select models on the Models page and use Test selected." />;
  }
  const current = selected ?? runs.data[0]?.id ?? null;

  return (
    <Grid gap="md">
      <Grid.Col span={{ base: 12, md: 4 }}>
        <ScrollArea.Autosize mah="70vh" offsetScrollbars>
          <Stack gap="xs">
            {runs.data.map((run) => (
              <RunListItem
                key={run.id}
                run={run}
                suiteName={suiteName(run.suite)}
                active={run.id === current}
                onSelect={() => setSelected(run.id)}
              />
            ))}
          </Stack>
        </ScrollArea.Autosize>
      </Grid.Col>
      <Grid.Col span={{ base: 12, md: 8 }}>
        {current !== null ? (
          <RunDetail key={current} runId={current} suiteName={suiteName} onDeleted={() => setSelected(null)} />
        ) : (
          <EmptyState message="Pick a run." />
        )}
      </Grid.Col>
    </Grid>
  );
}

/** Test run history on the left, the selected run's progress, summaries and results on the right. */
export function TestRunsModal({
  opened,
  initialRunId,
  onClose,
}: {
  opened: boolean;
  initialRunId: number | null;
  onClose: () => void;
}) {
  const mobile = useMediaQuery('(max-width: 48em)');
  return (
    <Modal opened={opened} onClose={onClose} title="Test runs" size="xl" fullScreen={mobile}>
      {opened && <RunsBody key={initialRunId ?? 'latest'} initialRunId={initialRunId} />}
    </Modal>
  );
}
