import { Button, Group, Menu, Stack, Text, Title } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { ChevronDown, FlaskConical, History, RefreshCw } from 'lucide-react';
import { useCallback, useDeferredValue, useMemo, useState } from 'react';
import { useEventStream } from '../api/eventStream';
import { useModels } from '../api/models';
import { useProviders } from '../api/providers';
import { useSettings } from '../api/settings';
import { useTriggerRefresh } from '../api/system';
import type { RefreshKind } from '../api/types';
import { ChangeOnly } from '../components/ChangeOnly';
import { ErrorState, LoadingState } from '../components/DataState';
import { ModelDetailsModal } from '../components/ModelDetailsModal';
import { BalancesStrip } from '../components/models/BalancesStrip';
import { ModelFiltersBar } from '../components/models/ModelFiltersBar';
import { ModelTable } from '../components/models/ModelTable';
import { RunTestsModal } from '../components/RunTestsModal';
import { SnippetModal } from '../components/SnippetModal';
import { TestRunsModal } from '../components/TestRunsModal';
import {
  applicableFilters,
  buildDisplayRows,
  DEFAULT_FILTERS,
  defaultDir,
  effectiveSort,
  enrichModels,
  filterModels,
  foldOpenCodeOffers,
  hasOpenCodeKey,
  MODEL_FILTERS_KEY,
  sanitizeFilters,
  sortModels,
  type ModelFilters,
  type SortKey,
} from '../lib/modelTable';
import { usePersistentState } from '../lib/usePersistentState';

const REFRESH_ITEMS: { label: string; kinds: RefreshKind[] }[] = [
  { label: 'Refresh everything', kinds: ['models', 'balances', 'benchmarks'] },
  { label: 'Models & prices', kinds: ['models'] },
  { label: 'Balances', kinds: ['balances'] },
  { label: 'Benchmarks', kinds: ['benchmarks'] },
];

function RefreshMenu() {
  const refresh = useTriggerRefresh();
  const { markRequested } = useEventStream();
  return (
    <ChangeOnly reason="Log in to refresh">
      {(blocked) => (
        <Menu position="bottom-end" withinPortal disabled={blocked}>
          <Menu.Target>
            <Button
              variant="default"
              leftSection={<RefreshCw size={16} />}
              rightSection={<ChevronDown size={14} />}
              loading={refresh.isPending}
              disabled={blocked}
            >
              Refresh
            </Button>
          </Menu.Target>
          <Menu.Dropdown>
            {REFRESH_ITEMS.map((item) => (
              <Menu.Item
                key={item.label}
                onClick={() => {
                  markRequested(item.kinds);
                  refresh.mutate(item.kinds, {
                    onError: (error) => notifications.show({ message: error.message, color: 'red' }),
                  });
                }}
              >
                {item.label}
              </Menu.Item>
            ))}
          </Menu.Dropdown>
        </Menu>
      )}
    </ChangeOnly>
  );
}

export function ModelsPage() {
  const models = useModels();
  const providers = useProviders();
  const settings = useSettings();

  const [filters, setFilters] = usePersistentState<ModelFilters>(MODEL_FILTERS_KEY, DEFAULT_FILTERS, sanitizeFilters);
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());
  const [selected, setSelected] = useState<ReadonlySet<number>>(new Set());
  const [detailId, setDetailId] = useState<number | null>(null);
  const [snippetId, setSnippetId] = useState<number | null>(null);
  const [runTestsOpen, setRunTestsOpen] = useState(false);
  const [runsModal, setRunsModal] = useState<{ runId: number | null } | null>(null);

  const data = models.data;
  const profiles = useMemo(() => data?.profiles ?? [], [data]);
  const profile = useMemo(
    () =>
      profiles.find((p) => p.id === filters.profileId) ??
      profiles.find((p) => p.id === data?.default_profile) ??
      profiles[0],
    [profiles, filters.profileId, data?.default_profile],
  );

  const freeModelCost = settings.data?.settings.scoring?.free_model_cost ?? 0.01;
  const enriched = useMemo(
    () => (data && profile ? enrichModels(data.models, profile, { freeModelCost }) : []),
    [data, profile, freeModelCost],
  );

  const search = useDeferredValue(filters.search);
  const opencodeKey = hasOpenCodeKey(providers.data);
  const { chips, nonBatchOnly, providers: providerFilter, minContext, groupBy } = filters;
  const { hideRestricted } = applicableFilters(filters, opencodeKey);
  const filtered = useMemo(
    () =>
      filterModels(enriched, {
        ...DEFAULT_FILTERS,
        search,
        chips,
        nonBatchOnly,
        hideRestricted,
        providers: providerFilter,
        minContext,
      }),
    [enriched, search, chips, nonBatchOnly, hideRestricted, providerFilter, minContext],
  );
  // Fold the Go copy of a model into its Console row while both providers are in view.
  const folded = useMemo(() => foldOpenCodeOffers(filtered), [filtered]);
  const bestValue = chips.includes('best');
  const sort = useMemo(
    () => effectiveSort({ ...DEFAULT_FILTERS, chips: bestValue ? ['best'] : [], sort: filters.sort }),
    [bestValue, filters.sort],
  );
  const sorted = useMemo(() => sortModels(folded, sort), [folded, sort]);
  const displayRows = useMemo(() => buildDisplayRows(sorted, groupBy, collapsed), [sorted, groupBy, collapsed]);

  const providerOptions = useMemo(() => {
    const seen = new Map<string, string>();
    for (const provider of providers.data ?? []) {
      if (provider.enabled) {
        seen.set(String(provider.id), provider.name);
      }
    }
    for (const row of data?.models ?? []) {
      seen.set(String(row.provider_id), row.provider_name);
    }
    return [...seen].map(([value, label]) => ({ value, label })).sort((a, b) => a.label.localeCompare(b.label));
  }, [providers.data, data]);

  const testableVisible = useMemo(() => folded.filter((m) => m.row.testable).map((m) => m.row.id), [folded]);
  const selectedVisible = testableVisible.filter((id) => selected.has(id)).length;

  const onSort = useCallback(
    (key: SortKey) =>
      setFilters((old) => {
        const best = old.chips.includes('best');
        const chipsAfter = best
          ? [...old.chips.filter((c) => c !== 'best' && c !== 'scored'), 'scored' as const]
          : old.chips;
        const current = effectiveSort(old);
        const dir = current.key === key ? (current.dir === 'asc' ? 'desc' : 'asc') : defaultDir(key);
        return { ...old, chips: chipsAfter, sort: { key, dir } };
      }),
    [setFilters],
  );

  const onToggle = useCallback(
    (id: number) =>
      setSelected((old) => {
        const next = new Set(old);
        if (next.has(id)) {
          next.delete(id);
        } else {
          next.add(id);
        }
        return next;
      }),
    [],
  );

  const onToggleAll = useCallback(() => {
    setSelected((old) => {
      const allSelected = testableVisible.length > 0 && testableVisible.every((id) => old.has(id));
      const next = new Set(old);
      for (const id of testableVisible) {
        if (allSelected) {
          next.delete(id);
        } else {
          next.add(id);
        }
      }
      return next;
    });
  }, [testableVisible]);

  const onToggleGroup = useCallback(
    (key: string) =>
      setCollapsed((old) => {
        const next = new Set(old);
        if (next.has(key)) {
          next.delete(key);
        } else {
          next.add(key);
        }
        return next;
      }),
    [],
  );

  const handlers = useMemo(() => ({ onToggle, onOpen: setDetailId, onConfig: setSnippetId }), [onToggle]);

  const selectedRows = useMemo(() => (data?.models ?? []).filter((row) => selected.has(row.id)), [data, selected]);

  const attributions = useMemo(
    () => (settings.data?.sources ?? []).filter((source) => source.enabled).map((source) => source.attribution),
    [settings.data],
  );

  return (
    <Stack gap="lg">
      <Group justify="space-between">
        <Title order={2}>Models</Title>
        <Group gap="sm">
          <Button variant="default" leftSection={<History size={16} />} onClick={() => setRunsModal({ runId: null })}>
            Test runs
          </Button>
          <ChangeOnly reason="Log in to run tests">
            {(blocked) => (
              <Button
                leftSection={<FlaskConical size={16} />}
                disabled={blocked || selectedRows.length === 0}
                onClick={() => setRunTestsOpen(true)}
              >
                Test selected ({selectedRows.length})
              </Button>
            )}
          </ChangeOnly>
          <RefreshMenu />
        </Group>
      </Group>

      {providers.data && <BalancesStrip providers={providers.data} />}

      <ModelFiltersBar
        filters={filters}
        onChange={setFilters}
        profiles={profiles}
        profile={profile}
        providers={providerOptions}
        showHideRestricted={opencodeKey}
      />

      {models.isPending && <LoadingState label="Loading models…" />}
      {models.isError && <ErrorState message={models.error.message} />}
      {data && profile && (
        <Stack gap={6}>
          <ModelTable
            rows={displayRows}
            profile={profile}
            sort={sort}
            onSort={onSort}
            selected={selected}
            headerChecked={testableVisible.length > 0 && selectedVisible === testableVisible.length}
            headerIndeterminate={selectedVisible > 0 && selectedVisible < testableVisible.length}
            headerDisabled={testableVisible.length === 0}
            onToggleAll={onToggleAll}
            onToggleGroup={onToggleGroup}
            handlers={handlers}
            emptyMessage={
              data.models.length === 0
                ? 'No models yet. Enable a provider in Settings, or wait for the first refresh.'
                : 'No models match these filters.'
            }
          />
          <Text size="sm" c="dimmed">
            Showing {filtered.length.toLocaleString()} of {data.models.length.toLocaleString()} models · selected{' '}
            {selected.size}
          </Text>
          <Text size="xs" c="dimmed">
            Prices: provider APIs and pricing pages, models.dev
            {attributions.length > 0 ? ` · Scores: ${attributions.join(' · ')}` : ''}
          </Text>
        </Stack>
      )}

      <ModelDetailsModal
        modelId={detailId}
        profile={profile}
        onChangeModel={setDetailId}
        onClose={() => setDetailId(null)}
      />
      <SnippetModal modelId={snippetId} onClose={() => setSnippetId(null)} />
      <RunTestsModal
        opened={runTestsOpen}
        targets={selectedRows}
        onRemove={onToggle}
        onClose={() => setRunTestsOpen(false)}
        onStarted={(runId) => {
          setRunTestsOpen(false);
          setRunsModal({ runId });
        }}
      />
      <TestRunsModal
        opened={runsModal !== null}
        initialRunId={runsModal?.runId ?? null}
        onClose={() => setRunsModal(null)}
      />
    </Stack>
  );
}
