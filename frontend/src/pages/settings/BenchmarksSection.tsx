import {
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Checkbox,
  Group,
  Modal,
  Stack,
  Switch,
  Text,
  Title,
  Tooltip,
} from '@mantine/core';
import { ExternalLink, ListChecks, TriangleAlert } from 'lucide-react';
import { useMemo, useState } from 'react';
import type { BenchmarkSourceOut } from '../../api/types';
import { ErrorState, LoadingState } from '../../components/DataState';
import { SecretInput } from '../../components/SecretInput';
import { relativeTime } from '../../lib/format';
import { ReviewUnmatchedModal } from './ReviewUnmatchedModal';
import { SaveBar } from './SaveBar';
import { useSettingsDraft } from './useSettingsDraft';

const DIMENSION_COLORS: Record<string, string> = { intelligence: 'blue', coding: 'teal', agentic: 'grape' };

function SourceCard({
  source,
  enabled,
  keyValue,
  onToggle,
  onKey,
}: {
  source: BenchmarkSourceOut;
  enabled: boolean;
  keyValue: string | null;
  onToggle: (enabled: boolean) => void;
  onKey: (value: string | null) => void;
}) {
  return (
    <Card withBorder padding="md">
      <Stack gap="sm">
        <Group justify="space-between" align="flex-start" wrap="nowrap">
          <Stack gap={2}>
            <Group gap="xs">
              <Title order={4}>{source.name}</Title>
              <Anchor href={source.homepage} target="_blank" rel="noreferrer" aria-label={`${source.name} homepage`}>
                <ExternalLink size={14} />
              </Anchor>
              {source.terms_warning && (
                <Tooltip label={source.terms_warning} multiline maw={360} withArrow>
                  <Badge size="xs" color="orange" variant="light">
                    Terms apply
                  </Badge>
                </Tooltip>
              )}
            </Group>
            <Text size="sm">{source.description}</Text>
          </Stack>
          <Switch
            aria-label={`Enable ${source.name}`}
            checked={enabled}
            onChange={(e) => onToggle(e.currentTarget.checked)}
          />
        </Group>
        <Group gap={6}>
          {source.metrics.map((metric) => (
            <Tooltip key={metric.id} label={metric.description || metric.label} multiline maw={320} withArrow>
              <Badge size="sm" variant="light" color={DIMENSION_COLORS[metric.dimension] ?? 'gray'}>
                {metric.label} ×{metric.weight}
              </Badge>
            </Tooltip>
          ))}
        </Group>
        {source.requires_key && (
          <SecretInput
            label={source.key_label ?? 'API key'}
            description={
              source.key_url ? (
                <Anchor href={source.key_url} target="_blank" rel="noreferrer" size="xs">
                  Get a key
                </Anchor>
              ) : undefined
            }
            stored={source.key_set}
            value={keyValue}
            onChange={onKey}
          />
        )}
        <Text size="xs" c="dimmed">
          Licence: {source.license} · {source.attribution}
        </Text>
        <Text size="xs" c="dimmed">
          {source.records.toLocaleString()} rows · {source.matched.toLocaleString()} matched ·{' '}
          {source.fetched_at ? `fetched ${relativeTime(source.fetched_at)}` : 'not fetched yet'}
        </Text>
      </Stack>
    </Card>
  );
}

export function BenchmarksSection() {
  const { query, value, update, dirty, submit, saving, reset } = useSettingsDraft(['benchmarks']);
  const [keys, setKeys] = useState<Record<string, string | null>>({});
  const [terms, setTerms] = useState<BenchmarkSourceOut | null>(null);
  const [accepted, setAccepted] = useState(false);
  const [reviewing, setReviewing] = useState(false);

  const sources = useMemo(() => query.data?.sources ?? [], [query.data]);
  const sourceNames = useMemo(() => new Map(sources.map((s) => [s.id, s.name])), [sources]);
  const keyEdits = Object.fromEntries(Object.entries(keys).filter(([, v]) => v !== null));
  const changed = dirty || Object.keys(keyEdits).length > 0;

  if (query.isPending) {
    return <LoadingState />;
  }
  if (query.isError || value === null) {
    return <ErrorState message={query.error?.message} />;
  }

  const enabledOf = (source: BenchmarkSourceOut) => value.benchmarks.sources[source.id]?.enabled ?? source.enabled;

  const setEnabled = (source: BenchmarkSourceOut, enabled: boolean, acknowledge = false) =>
    update((old) => ({
      ...old,
      benchmarks: {
        ...old.benchmarks,
        aa_terms_acknowledged: old.benchmarks.aa_terms_acknowledged || acknowledge,
        sources: {
          ...old.benchmarks.sources,
          [source.id]: { ...old.benchmarks.sources[source.id], enabled },
        },
      },
    }));

  return (
    <Stack gap="lg">
      <Stack gap={4}>
        <Title order={3}>Benchmarks</Title>
        <Text size="sm" c="dimmed">
          Third-party leaderboards Vibonomics folds into the intelligence, coding and agentic scores. Data is fetched at
          runtime from each source; check each licence before enabling it.
        </Text>
      </Stack>

      <Group justify="flex-end">
        <Button variant="default" leftSection={<ListChecks size={16} />} onClick={() => setReviewing(true)}>
          Review unmatched
        </Button>
      </Group>

      {sources.map((source) => (
        <SourceCard
          key={source.id}
          source={source}
          enabled={enabledOf(source)}
          keyValue={keys[source.id] ?? null}
          onKey={(next) => setKeys((old) => ({ ...old, [source.id]: next }))}
          onToggle={(enabled) => {
            if (enabled && source.terms_warning && !value.benchmarks.aa_terms_acknowledged) {
              setAccepted(false);
              setTerms(source);
            } else {
              setEnabled(source, enabled);
            }
          }}
        />
      ))}

      <SaveBar
        dirty={changed}
        saving={saving}
        onReset={() => {
          reset();
          setKeys({});
        }}
        onSave={() => submit({ benchmarkKeys: keyEdits, onSaved: () => setKeys({}) })}
      />

      <Modal opened={terms !== null} onClose={() => setTerms(null)} title="Terms of use">
        <Stack gap="md">
          <Alert color="orange" icon={<TriangleAlert size={16} />}>
            {terms?.terms_warning}
          </Alert>
          <Checkbox
            label="I understand and accept responsibility"
            checked={accepted}
            onChange={(event) => setAccepted(event.currentTarget.checked)}
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setTerms(null)}>
              Cancel
            </Button>
            <Button
              disabled={!accepted}
              onClick={() => {
                if (terms !== null) {
                  setEnabled(terms, true, true);
                }
                setTerms(null);
              }}
            >
              Enable
            </Button>
          </Group>
        </Stack>
      </Modal>

      <ReviewUnmatchedModal opened={reviewing} onClose={() => setReviewing(false)} sourceNames={sourceNames} />
    </Stack>
  );
}
