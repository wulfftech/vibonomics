import { ActionIcon, Badge, Card, Checkbox, Group, Table, Text, Tooltip, UnstyledButton } from '@mantine/core';
import { useVirtualizer } from '@tanstack/react-virtual';
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronDown, ChevronRight, Code } from 'lucide-react';
import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { UsageProfile } from '../../api/types';
import { layoutColumns, type ColumnSpec } from '../../lib/autoWidths';
import { compactTokens, DASH, money, relativeTime } from '../../lib/format';
import type { DisplayRow, EnrichedModel, GroupSummary, SortKey, SortState } from '../../lib/modelTable';
import { modelBadgeLabels, modelSubline } from '../../lib/modelText';
import { CopyIconButton } from '../CopyIconButton';
import { EmptyState } from '../DataState';
import { providerTextColor } from '../../lib/providerColors';
import { ProviderDot } from '../ProviderDot';
import { BlendedCell, CompositeCell, DiscountCell, ModelNameCell, ScoreNumber, TaskCostCell, ValueCell } from './cells';

interface Column extends ColumnSpec {
  label?: string;
  sort?: SortKey;
}

const COLUMNS: Column[] = [
  { id: 'select', min: 40, max: 40 },
  { id: 'model', label: 'Model', sort: 'name', min: 250, max: 640, grow: 0.55 },
  { id: 'provider', label: 'Provider', sort: 'provider', min: 110, max: 210, grow: 0.1 },
  { id: 'modelId', label: 'Model ID', min: 145, max: 330, grow: 0.3 },
  { id: 'blended', label: 'Blended $/1M', sort: 'blended', min: 122, max: 175, grow: 0.05 },
  { id: 'discount', label: 'Discount', min: 86, max: 112 },
  { id: 'inout', label: 'In / Out', min: 112, max: 170 },
  { id: 'task', label: '$/task', sort: 'taskCost', min: 88, max: 120 },
  { id: 'intel', label: 'Intel', sort: 'intelligence', min: 62, max: 84 },
  { id: 'coding', label: 'Coding', sort: 'coding', min: 72, max: 96 },
  { id: 'agentic', label: 'Agentic', sort: 'agentic', min: 78, max: 104 },
  { id: 'score', label: 'Score', sort: 'score', min: 112, max: 170, grow: 0.05 },
  { id: 'value', label: 'Value', sort: 'value', min: 84, max: 110 },
  { id: 'context', label: 'Context', sort: 'context', min: 78, max: 100 },
  { id: 'released', min: 112, max: 150 },
  { id: 'config', min: 44, max: 44 },
];

const TABLE_MIN_WIDTH = COLUMNS.reduce((sum, column) => sum + column.min, 0);

const CANVAS_FALLBACK = 7;
let textContext: CanvasRenderingContext2D | null | undefined;

function textWidth(text: string, mono = false, weight = 400, size = 12): number {
  if (text === '' || typeof document === 'undefined') {
    return text.length * CANVAS_FALLBACK;
  }
  if (textContext === undefined) {
    textContext = document.createElement('canvas').getContext('2d');
  }
  if (textContext === null) {
    return text.length * CANVAS_FALLBACK;
  }
  textContext.font = `${weight} ${size}px ${mono ? 'ui-monospace, SFMono-Regular, Menlo, monospace' : 'Inter, sans-serif'}`;
  return textContext.measureText(text).width;
}

function widest(values: Iterable<string>, mono = false, weight = 400): number {
  let width = 0;
  for (const value of values) {
    width = Math.max(width, textWidth(value, mono, weight));
  }
  return width;
}

const BADGE_GAP = 6;
/** Mantine's xs badge: 6px of padding either side and a 1px border. */
const BADGE_CHROME = 14;
const CELL_PADDING = 24;
/** The own-test mark (a 12 px icon) and the gap before it, in the score cells. */
const MARK_WIDTH = 16;

/** The widest of a model's name line (name plus its badges) and its subline, as drawn by `ModelNameCell`. */
function modelCellWidth(model: EnrichedModel): number {
  const { row } = model;
  const badges = modelBadgeLabels(row, model.alsoOnGo).reduce(
    (sum, label) => sum + BADGE_GAP + textWidth(label, false, 700, 9) + BADGE_CHROME,
    0,
  );
  const nameLine = textWidth(row.name, false, 500, 14) + badges;
  return Math.max(nameLine, textWidth(modelSubline(row), false, 400, 12)) + CELL_PADDING;
}

/** Content-sized widths for the table, measured once per data set. */
function measureColumns(models: EnrichedModel[]): Record<string, number> {
  const rows = models.map((model) => model.row);
  return {
    select: 40,
    model: models.reduce((width, model) => Math.max(width, modelCellWidth(model)), 0),
    provider: widest(rows.map((row) => row.provider_name)) + 30,
    modelId:
      widest(
        rows.map((row) => row.model_id),
        true,
      ) + 36,
    discount: textWidth('Discount', false, 600) + 24,
    blended:
      widest(
        models.map((model) =>
          model.blended === null
            ? DASH
            : model.listBlended !== null && model.listBlended > model.blended
              ? `${money(model.blended)} ${money(model.listBlended)}`
              : money(model.blended),
        ),
      ) + 24,
    inout: widest(rows.map((row) => `${money(row.price.input)} / ${money(row.price.output)}`)) + 16,
    task: textWidth('$123.45', false, 500) + 34,
    intel: textWidth('Intel', false, 600) + 22,
    coding: textWidth('Coding', false, 600) + 22,
    agentic: textWidth('Agentic', false, 600) + 22,
    score: textWidth('100 ·3 tested 100', false, 600) + 20 + MARK_WIDTH,
    value: textWidth('Value', false, 600) + 28,
    context: widest(rows.map((row) => compactTokens(row.context_length))) + 24,
    released: Math.max(textWidth('2026-10-24'), textWidth('seen 12mo ago')) + 18,
    config: 44,
  };
}

const HEADER_HEIGHT = 37;
const ROW_HEIGHT = 45;
const GROUP_ROW_HEIGHT = 41;
const NOT_TESTABLE = 'Add an API key for this provider to test it';
const NOT_TESTABLE_OPENCODE_FREE = 'Free OpenCode models only run inside the OpenCode client. Pick a paid model or Go.';

function SortButton({
  label,
  sortKey,
  sort,
  onSort,
}: {
  label: string;
  sortKey: SortKey;
  sort: SortState;
  onSort: (key: SortKey) => void;
}) {
  const active = sort.key === sortKey;
  return (
    <UnstyledButton onClick={() => onSort(sortKey)} fz="sm" fw={600}>
      <Group gap={4} wrap="nowrap">
        {label}
        {active ? (
          sort.dir === 'asc' ? (
            <ArrowUp size={14} />
          ) : (
            <ArrowDown size={14} />
          )
        ) : (
          <ArrowUpDown size={14} opacity={0.4} />
        )}
      </Group>
    </UnstyledButton>
  );
}

function ariaSort(sort: SortState, keys: SortKey[]): 'ascending' | 'descending' | 'none' {
  return keys.includes(sort.key) ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none';
}

interface RowHandlers {
  onToggle: (id: number) => void;
  onOpen: (id: number) => void;
  onConfig: (id: number) => void;
}

const ModelTr = memo(function ModelTr({
  model,
  profile,
  selected,
  index,
  measure,
  handlers,
}: {
  model: EnrichedModel;
  profile: UsageProfile;
  selected: boolean;
  index: number;
  measure: (node: Element | null) => void;
  handlers: RowHandlers;
}) {
  const { row } = model;
  const stop = (event: React.SyntheticEvent) => event.stopPropagation();
  const checkbox = (
    <Checkbox
      size="xs"
      aria-label={`Select ${row.name} (${row.provider_name})`}
      checked={selected}
      disabled={!row.testable}
      onChange={() => handlers.onToggle(row.id)}
    />
  );
  return (
    <Table.Tr
      ref={measure}
      data-index={index}
      tabIndex={0}
      style={{ cursor: 'pointer' }}
      bg={selected ? 'var(--mantine-primary-color-light)' : undefined}
      onClick={() => handlers.onOpen(row.id)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' && event.target === event.currentTarget) {
          handlers.onOpen(row.id);
        }
      }}
    >
      <Table.Td onClick={stop}>
        {row.testable ? (
          checkbox
        ) : (
          <Tooltip
            label={row.provider_type === 'opencode' && row.is_free ? NOT_TESTABLE_OPENCODE_FREE : NOT_TESTABLE}
            multiline
            maw={280}
            withArrow
          >
            <span style={{ display: 'inline-block' }}>{checkbox}</span>
          </Tooltip>
        )}
      </Table.Td>
      <Table.Td>
        <ModelNameCell row={row} alsoOnGo={model.alsoOnGo} />
      </Table.Td>
      <Table.Td>
        <Group gap={6} wrap="nowrap" miw={0}>
          <ProviderDot type={row.provider_type} />
          <Text size="sm" truncate="end" style={{ color: providerTextColor(row.provider_type) }}>
            {row.provider_name}
          </Text>
        </Group>
      </Table.Td>
      <Table.Td>
        <Group gap={2} wrap="nowrap" miw={0}>
          <Tooltip label={row.model_id} withArrow openDelay={300}>
            <Text ff="monospace" size="xs" truncate="end" miw={0} style={{ flex: 1 }}>
              {row.model_id}
            </Text>
          </Tooltip>
          <CopyIconButton value={row.model_id} label="Copy model ID" size="xs" />
        </Group>
      </Table.Td>
      <Table.Td>
        <BlendedCell model={model} profile={profile} />
      </Table.Td>
      <Table.Td>
        <DiscountCell row={row} />
      </Table.Td>
      <Table.Td>
        <Text size="xs">
          {row.price.input === null && row.price.output === null
            ? DASH
            : `${money(row.price.input)} / ${money(row.price.output)}`}
        </Text>
      </Table.Td>
      <Table.Td>
        <TaskCostCell model={model} profile={profile} />
      </Table.Td>
      <Table.Td>
        <ScoreNumber value={row.scores.intelligence} />
      </Table.Td>
      <Table.Td>
        <ScoreNumber value={row.scores.coding} ownTest={row.scores.own_test_preferred} />
      </Table.Td>
      <Table.Td>
        <ScoreNumber value={row.scores.agentic} ownTest={row.scores.own_test_preferred} />
      </Table.Td>
      <Table.Td>
        <CompositeCell row={row} />
      </Table.Td>
      <Table.Td>
        <ValueCell model={model} />
      </Table.Td>
      <Table.Td>
        <Text size="sm">{compactTokens(row.context_length)}</Text>
      </Table.Td>
      <Table.Td>
        <Text size="xs">{row.release_date ?? DASH}</Text>
        <Text size="xs" c="dimmed">
          seen {relativeTime(row.first_seen)}
        </Text>
      </Table.Td>
      <Table.Td onClick={stop}>
        <Tooltip label="Config snippets" withArrow>
          <ActionIcon
            variant="subtle"
            color="gray"
            aria-label={`Config snippets for ${row.name}`}
            onClick={() => handlers.onConfig(row.id)}
          >
            <Code size={16} />
          </ActionIcon>
        </Tooltip>
      </Table.Td>
    </Table.Tr>
  );
});

const GroupTr = memo(function GroupTr({
  group,
  index,
  measure,
  onToggle,
}: {
  group: GroupSummary;
  index: number;
  measure: (node: Element | null) => void;
  onToggle: (key: string) => void;
}) {
  const stats = [
    group.cheapestBlended !== null ? `from ${money(group.cheapestBlended)}/1M` : null,
    group.bestScore !== null ? `best score ${Math.round(group.bestScore)}` : null,
    group.bestValueIndex !== null ? `best value ${Math.round(group.bestValueIndex)}` : null,
  ].filter(Boolean);
  return (
    <Table.Tr ref={measure} data-index={index} bg="var(--mantine-color-default-hover)">
      <Table.Td colSpan={COLUMNS.length} py={6}>
        <UnstyledButton
          onClick={() => onToggle(group.key)}
          aria-expanded={!group.collapsed}
          aria-label={`${group.collapsed ? 'Expand' : 'Collapse'} ${group.label}`}
        >
          <Group gap="xs" wrap="nowrap">
            {group.collapsed ? <ChevronRight size={16} /> : <ChevronDown size={16} />}
            {group.colorType && <ProviderDot type={group.colorType} size={10} />}
            <Text size="sm" fw={600}>
              {group.label}
            </Text>
            {group.sublabel && (
              <Text size="xs" c="dimmed" ff="monospace">
                {group.sublabel}
              </Text>
            )}
            <Badge size="xs" variant="light" color="gray">
              {group.count}
            </Badge>
            {stats.length > 0 && (
              <Text size="xs" c="dimmed">
                {stats.join(' · ')}
              </Text>
            )}
          </Group>
        </UnstyledButton>
      </Table.Td>
    </Table.Tr>
  );
});

/** Sizes the scroll area to fill the viewport below it, leaving `reserve` px for the footer. */
function useFillHeight(reserve: number): [React.RefObject<HTMLDivElement | null>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState(600);
  useLayoutEffect(() => {
    const measure = () => {
      const node = ref.current;
      if (node === null) {
        return;
      }
      const top = node.getBoundingClientRect().top + window.scrollY;
      setHeight(Math.max(420, Math.round(window.innerHeight - top - reserve)));
    };
    measure();
    window.addEventListener('resize', measure);
    const observer = new ResizeObserver(measure);
    observer.observe(document.body);
    return () => {
      window.removeEventListener('resize', measure);
      observer.disconnect();
    };
  }, [reserve]);
  return [ref, height];
}

export function ModelTable({
  rows,
  profile,
  sort,
  onSort,
  selected,
  headerChecked,
  headerIndeterminate,
  headerDisabled,
  onToggleAll,
  onToggleGroup,
  handlers,
  emptyMessage,
}: {
  rows: DisplayRow[];
  profile: UsageProfile;
  sort: SortState;
  onSort: (key: SortKey) => void;
  selected: ReadonlySet<number>;
  headerChecked: boolean;
  headerIndeterminate: boolean;
  headerDisabled: boolean;
  onToggleAll: () => void;
  onToggleGroup: (key: string) => void;
  handlers: RowHandlers;
  emptyMessage: string;
}) {
  const [scrollRef, height] = useFillHeight(64);
  const models = useMemo(() => rows.flatMap((row) => (row.kind === 'model' ? [row.model] : [])), [rows]);
  const measured = useMemo(() => measureColumns(models), [models]);
  const [available, setAvailable] = useState(1200);
  useLayoutEffect(() => {
    const node = scrollRef.current;
    if (node === null) {
      return undefined;
    }
    const measure = () => setAvailable(node.clientWidth);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [scrollRef]);
  const widths = useMemo(() => layoutColumns(measured, COLUMNS, available), [measured, available]);
  const totalWidth = COLUMNS.reduce((sum, column) => sum + (widths[column.id] ?? column.min), 0);
  const tableWidth = Math.max(totalWidth, available);
  // The React Compiler isn't used in this build, and the rows are memoised by hand below.
  // eslint-disable-next-line react-hooks/incompatible-library
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: (i) => (rows[i]?.kind === 'group' ? GROUP_ROW_HEIGHT : ROW_HEIGHT),
    getItemKey: (i) => rows[i]?.key ?? i,
    overscan: 10,
    paddingStart: HEADER_HEIGHT,
  });

  // Jump back to the top when the rows change shape (new filter or sort), not on live refreshes.
  const firstKey = rows[0]?.key;
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 0 });
  }, [firstKey, scrollRef]);

  const items = virtualizer.getVirtualItems();
  const paddingTop = items.length > 0 ? items[0].start - HEADER_HEIGHT : 0;
  const paddingBottom = items.length > 0 ? virtualizer.getTotalSize() - items[items.length - 1].end : 0;

  return (
    <Card withBorder padding={0}>
      <div ref={scrollRef} style={{ height, overflow: 'auto' }}>
        <Table
          stickyHeader
          layout="fixed"
          miw={TABLE_MIN_WIDTH}
          verticalSpacing={6}
          horizontalSpacing="xs"
          highlightOnHover
          style={{ width: tableWidth }}
        >
          <colgroup>
            {COLUMNS.map((column) => (
              <col key={column.id} style={{ width: widths[column.id] ?? column.min }} />
            ))}
          </colgroup>
          <Table.Thead style={{ zIndex: 2 }}>
            <Table.Tr h={HEADER_HEIGHT}>
              <Table.Th>
                <Checkbox
                  size="xs"
                  aria-label="Select all visible testable models"
                  checked={headerChecked}
                  indeterminate={headerIndeterminate}
                  disabled={headerDisabled}
                  onChange={onToggleAll}
                />
              </Table.Th>
              {COLUMNS.slice(1).map((column) => {
                if (column.id === 'released') {
                  return (
                    <Table.Th key={column.id} aria-sort={ariaSort(sort, ['release', 'firstSeen'])}>
                      <Group gap={6} wrap="nowrap">
                        <SortButton label="Released" sortKey="release" sort={sort} onSort={onSort} />
                        <SortButton label="Seen" sortKey="firstSeen" sort={sort} onSort={onSort} />
                      </Group>
                    </Table.Th>
                  );
                }
                return (
                  <Table.Th key={column.id} aria-sort={column.sort ? ariaSort(sort, [column.sort]) : undefined}>
                    {column.sort && column.label ? (
                      <SortButton label={column.label} sortKey={column.sort} sort={sort} onSort={onSort} />
                    ) : (
                      column.label
                    )}
                  </Table.Th>
                );
              })}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {paddingTop > 0 && (
              <tr aria-hidden>
                <td colSpan={COLUMNS.length} style={{ height: paddingTop, padding: 0, border: 0 }} />
              </tr>
            )}
            {items.map((item) => {
              const entry = rows[item.index];
              if (entry === undefined) {
                return null;
              }
              return entry.kind === 'group' ? (
                <GroupTr
                  key={item.key}
                  group={entry.group}
                  index={item.index}
                  measure={virtualizer.measureElement}
                  onToggle={onToggleGroup}
                />
              ) : (
                <ModelTr
                  key={item.key}
                  model={entry.model}
                  profile={profile}
                  selected={selected.has(entry.model.row.id)}
                  index={item.index}
                  measure={virtualizer.measureElement}
                  handlers={handlers}
                />
              );
            })}
            {paddingBottom > 0 && (
              <tr aria-hidden>
                <td colSpan={COLUMNS.length} style={{ height: paddingBottom, padding: 0, border: 0 }} />
              </tr>
            )}
          </Table.Tbody>
        </Table>
        {rows.length === 0 && <EmptyState message={emptyMessage} />}
      </div>
    </Card>
  );
}
