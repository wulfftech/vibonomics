import { Button, Card, Checkbox, Group, Modal, Select, Stack, Table, Text, TextInput } from '@mantine/core';
import { useDebouncedValue, useMediaQuery } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { Search } from 'lucide-react';
import { useMemo, useState } from 'react';
import { useCanonicalSearch, useSetAlias, useUnmatchedRows } from '../../api/benchmarks';
import type { ScoreRow } from '../../api/types';
import { EmptyState, ErrorState, LoadingState } from '../../components/DataState';

const PAGE = 100;

function guessQuery(sourceModel: string): string {
  return sourceModel
    .replace(/\(.*?\)|\[.*?\]/g, ' ')
    .replace(/^[^/:]+[/:]\s*/, '')
    .trim()
    .toLowerCase()
    .slice(0, 40);
}

/** An async search over models.dev canonical ids; picking one maps the row at once. */
function CanonicalPicker({ row, onPick }: { row: ScoreRow; onPick: (id: string) => void }) {
  const [search, setSearch] = useState('');
  const [opened, setOpened] = useState(false);
  const [debounced] = useDebouncedValue(search, 250);
  const query = debounced.trim() || guessQuery(row.source_model);
  const results = useCanonicalSearch(query, opened);
  const data = (results.data ?? []).map((c) => ({ value: c.id, label: `${c.name} (${c.id})` }));

  return (
    <Select
      aria-label={`Map ${row.source_model} to a model`}
      placeholder="Search models…"
      size="xs"
      searchable
      clearable
      data={data}
      value={null}
      searchValue={search}
      onSearchChange={setSearch}
      onDropdownOpen={() => setOpened(true)}
      filter={({ options }) => options}
      nothingFoundMessage={results.isFetching ? 'Searching…' : 'No matching models'}
      onChange={(value) => {
        if (value) {
          onPick(value);
        }
      }}
      comboboxProps={{ withinPortal: true }}
    />
  );
}

/** Benchmark rows no canonical model matched: map each one, or ignore it. */
export function ReviewUnmatchedModal({
  opened,
  onClose,
  sourceNames,
}: {
  opened: boolean;
  onClose: () => void;
  sourceNames: Map<string, string>;
}) {
  const mobile = useMediaQuery('(max-width: 48em)');
  const rows = useUnmatchedRows(opened);
  const setAlias = useSetAlias();
  const [filter, setFilter] = useState('');
  const [showIgnored, setShowIgnored] = useState(false);

  const visible = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return (rows.data ?? []).filter(
      (row) =>
        (showIgnored || row.match_method !== 'operator') &&
        (needle === '' || row.source_model.toLowerCase().includes(needle) || row.source.includes(needle)),
    );
  }, [rows.data, filter, showIgnored]);

  const map = (row: ScoreRow, canonicalId: string | null) =>
    setAlias.mutate(
      { source: row.source, source_model: row.source_model, canonical_id: canonicalId },
      {
        onSuccess: () =>
          notifications.show({
            message: canonicalId ? `Mapped ${row.source_model} to ${canonicalId}` : `Ignoring ${row.source_model}`,
            color: 'green',
          }),
        onError: (error) => notifications.show({ message: error.message, color: 'red' }),
      },
    );

  return (
    <Modal opened={opened} onClose={onClose} title="Review unmatched benchmark rows" size="xl" fullScreen={mobile}>
      <Stack gap="md">
        <Text size="sm" c="dimmed">
          These leaderboard entries weren&apos;t matched to a known model, so their scores aren&apos;t used. Map each to
          its models.dev model, or ignore entries that will never match (retired models, aggregates).
        </Text>
        <Group align="flex-end">
          <TextInput
            label="Filter"
            leftSection={<Search size={14} />}
            w={220}
            value={filter}
            onChange={(event) => setFilter(event.currentTarget.value)}
          />
          <Checkbox
            label="Show ignored"
            checked={showIgnored}
            onChange={(event) => setShowIgnored(event.currentTarget.checked)}
            mb={8}
          />
        </Group>
        {rows.isPending && <LoadingState />}
        {rows.isError && <ErrorState message={rows.error.message} />}
        {rows.data && visible.length === 0 && <EmptyState message="Nothing to review." />}
        {visible.length > 0 && (
          <Card withBorder padding="md">
            <Table.ScrollContainer minWidth={720}>
              <Table verticalSpacing="xs">
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th w={150}>Source</Table.Th>
                    <Table.Th>Entry</Table.Th>
                    <Table.Th w={280}>Map to</Table.Th>
                    <Table.Th w={90} />
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {visible.slice(0, PAGE).map((row) => (
                    <Table.Tr key={`${row.source}:${row.source_model}`}>
                      <Table.Td>
                        <Text size="xs">{sourceNames.get(row.source) ?? row.source}</Text>
                      </Table.Td>
                      <Table.Td>
                        <Text size="sm">{row.source_model}</Text>
                        <Text size="xs" c="dimmed" lineClamp={1}>
                          {Object.entries(row.metrics)
                            .map(([label, value]) => `${label}: ${Number(value.toFixed(2))}`)
                            .join(' · ')}
                        </Text>
                      </Table.Td>
                      <Table.Td>
                        <CanonicalPicker row={row} onPick={(id) => map(row, id)} />
                      </Table.Td>
                      <Table.Td>
                        {row.match_method === 'operator' ? (
                          <Text size="xs" c="dimmed">
                            Ignored
                          </Text>
                        ) : (
                          <Button
                            variant="default"
                            size="xs"
                            disabled={setAlias.isPending}
                            onClick={() => map(row, null)}
                          >
                            Ignore
                          </Button>
                        )}
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
            {visible.length > PAGE && (
              <Text size="xs" c="dimmed" mt="xs">
                Showing {PAGE} of {visible.length}; filter to narrow the list.
              </Text>
            )}
          </Card>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Close
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
