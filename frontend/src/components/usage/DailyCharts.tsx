import { Card, Group, Paper, SegmentedControl, SimpleGrid, Table, Text, Title } from '@mantine/core';
import { useElementSize } from '@mantine/hooks';
import { useState, type KeyboardEvent, type PointerEvent, type ReactNode } from 'react';
import type { SpendDay } from '../../api/types';
import { DASH, integer, money, percent } from '../../lib/format';
import {
  CHART,
  axisMoney,
  axisTicks,
  bigCount,
  columnPath,
  dayLabel,
  labelIndexes,
  linePath,
  longDay,
} from '../../lib/usage';

const HEIGHT = 200;
const MARGIN = { top: 10, right: 12, bottom: 26, left: 50 };
const TOOLTIP_WIDTH = 200;

type ChartId = 'spend' | 'efficiency';
interface Active {
  index: number;
  chart: ChartId;
}

interface Geometry {
  width: number;
  plotWidth: number;
  plotHeight: number;
  band: number;
  x: (index: number) => number;
  y: (value: number, top: number) => number;
}

function geometry(width: number, count: number): Geometry {
  const plotWidth = Math.max(10, width - MARGIN.left - MARGIN.right);
  const plotHeight = HEIGHT - MARGIN.top - MARGIN.bottom;
  const band = plotWidth / Math.max(count, 1);
  return {
    width,
    plotWidth,
    plotHeight,
    band,
    x: (index) => MARGIN.left + (index + 0.5) * band,
    y: (value, top) => MARGIN.top + plotHeight - (value / top) * plotHeight,
  };
}

function Axes({ g, ticks, top, days }: { g: Geometry; ticks: number[]; top: number; days: SpendDay[] }) {
  return (
    <g aria-hidden="true">
      {ticks.map((tick) => (
        <g key={tick}>
          <line
            x1={MARGIN.left}
            x2={MARGIN.left + g.plotWidth}
            y1={g.y(tick, top)}
            y2={g.y(tick, top)}
            style={{ stroke: tick === 0 ? CHART.axis : CHART.grid }}
            strokeWidth={1}
          />
          <text
            x={MARGIN.left - 8}
            y={g.y(tick, top)}
            textAnchor="end"
            dominantBaseline="middle"
            fontSize={11}
            style={{ fill: CHART.muted }}
          >
            {axisMoney(tick)}
          </text>
        </g>
      ))}
      {labelIndexes(days.length, g.plotWidth).map((i) => (
        <text
          key={days[i]?.date}
          x={g.x(i)}
          y={HEIGHT - 8}
          textAnchor="middle"
          fontSize={11}
          style={{ fill: CHART.muted }}
        >
          {dayLabel(days[i]?.date ?? '')}
        </text>
      ))}
    </g>
  );
}

function Tooltip({ day, left, top }: { day: SpendDay; left: number; top: number }) {
  return (
    <Paper
      withBorder
      shadow="sm"
      p="xs"
      style={{ position: 'absolute', left, top, width: TOOLTIP_WIDTH, pointerEvents: 'none', zIndex: 5 }}
    >
      <Text size="xs" c="dimmed">
        {longDay(day.date)}
      </Text>
      <Group gap={6} wrap="nowrap" align="center">
        <svg width={14} height={4} aria-hidden="true">
          <line x1={0} x2={14} y1={2} y2={2} strokeWidth={2} strokeLinecap="round" style={{ stroke: CHART.series }} />
        </svg>
        <Text fw={700}>{money(day.cost)}</Text>
        <Text size="xs" c="dimmed">
          spent
        </Text>
      </Group>
      <Text size="xs">
        {bigCount(day.tokens)} tokens, {integer(day.requests)} requests
      </Text>
      <Text size="xs">
        {day.cost_per_mtok === null ? DASH : money(day.cost_per_mtok)} per 1M tokens
        {day.cache_hit === null ? '' : `, ${percent(day.cache_hit)} cached`}
      </Text>
    </Paper>
  );
}

interface FrameProps {
  days: SpendDay[];
  chart: ChartId;
  active: Active | null;
  onActive: (next: Active | null) => void;
  label: string;
  children: (g: Geometry, activeIndex: number | null) => ReactNode;
}

/** The svg, its pointer and keyboard handling, and the tooltip. Each chart draws its marks inside. */
function Frame({ days, chart, active, onActive, label, children }: FrameProps) {
  const { ref, width } = useElementSize();
  const g = geometry(width || 320, days.length);
  const last = days.length - 1;
  const activeIndex = active === null ? null : Math.min(active.index, last);
  const tipDay = active?.chart === chart && activeIndex !== null ? days[activeIndex] : undefined;

  const move = (event: PointerEvent<SVGRectElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    const fraction = box.width > 0 ? (event.clientX - box.left) / box.width : 0;
    onActive({ index: Math.max(0, Math.min(last, Math.floor(fraction * days.length))), chart });
  };
  const key = (event: KeyboardEvent<HTMLDivElement>) => {
    const from = activeIndex ?? last;
    const next =
      event.key === 'ArrowLeft'
        ? from - 1
        : event.key === 'ArrowRight'
          ? from + 1
          : event.key === 'Home'
            ? 0
            : event.key === 'End'
              ? last
              : null;
    if (next !== null) {
      event.preventDefault();
      onActive({ index: Math.max(0, Math.min(last, next)), chart });
    }
  };

  return (
    <div
      ref={ref}
      role="group"
      aria-label={`${label}. Use the left and right arrow keys to read each day, or switch to the table.`}
      tabIndex={0}
      onKeyDown={key}
      onFocus={() => active === null && onActive({ index: last, chart })}
      onBlur={() => onActive(null)}
      style={{ position: 'relative', outline: 'none' }}
    >
      <svg width={g.width} height={HEIGHT} role="img" aria-label={label} style={{ display: 'block' }}>
        {children(g, activeIndex)}
        <rect
          x={MARGIN.left}
          y={MARGIN.top}
          width={g.plotWidth}
          height={g.plotHeight}
          fill="transparent"
          onPointerMove={move}
          onPointerLeave={() => onActive(null)}
        />
      </svg>
      {tipDay !== undefined && activeIndex !== null && (
        <Tooltip
          day={tipDay}
          left={Math.max(0, Math.min(g.width - TOOLTIP_WIDTH, g.x(activeIndex) + 14))}
          top={MARGIN.top}
        />
      )}
    </div>
  );
}

type ChartProps = Omit<FrameProps, 'children' | 'chart' | 'label'>;

function SpendColumns(props: ChartProps) {
  const { days } = props;
  const { top, ticks } = axisTicks(Math.max(0, ...days.map((d) => d.cost)));
  return (
    <Frame {...props} chart="spend" label="Spend per day, in dollars">
      {(g, activeIndex) => (
        <>
          <Axes g={g} ticks={ticks} top={top} days={days} />
          {days.map((d, i) => {
            const width = Math.max(1, Math.min(24, g.band - 2));
            const dim = activeIndex !== null && activeIndex !== i;
            return (
              <path
                key={d.date}
                d={columnPath(g.x(i) - width / 2, width, g.y(d.cost, top), g.y(0, top))}
                style={{ fill: CHART.series }}
                opacity={dim ? 0.55 : 1}
              />
            );
          })}
        </>
      )}
    </Frame>
  );
}

function EfficiencyLine(props: ChartProps) {
  const { days } = props;
  const { top, ticks } = axisTicks(Math.max(0, ...days.map((d) => d.cost_per_mtok ?? 0)));
  return (
    <Frame {...props} chart="efficiency" label="Cost per million tokens, per day, in dollars">
      {(g, activeIndex) => {
        const points = days.map((d, i) =>
          d.cost_per_mtok === null ? null : { x: g.x(i), y: g.y(d.cost_per_mtok, top) },
        );
        const lastPoint = [...points].reverse().find((p) => p !== null) ?? null;
        const hovered = activeIndex === null ? null : (points[activeIndex] ?? null);
        const dot = hovered ?? lastPoint;
        return (
          <>
            <Axes g={g} ticks={ticks} top={top} days={days} />
            {activeIndex !== null && (
              <line
                x1={g.x(activeIndex)}
                x2={g.x(activeIndex)}
                y1={MARGIN.top}
                y2={MARGIN.top + g.plotHeight}
                strokeWidth={1}
                style={{ stroke: CHART.axis }}
              />
            )}
            <path
              d={linePath(points)}
              fill="none"
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
              style={{ stroke: CHART.series }}
            />
            {dot !== null && (
              <g>
                <circle cx={dot.x} cy={dot.y} r={6} style={{ fill: CHART.surface }} />
                <circle cx={dot.x} cy={dot.y} r={4} style={{ fill: CHART.series }} />
              </g>
            )}
          </>
        );
      }}
    </Frame>
  );
}

function DailyTable({ days }: { days: SpendDay[] }) {
  return (
    <Table.ScrollContainer minWidth={460} mah={240} type="native">
      <Table verticalSpacing={4} stickyHeader aria-label="Spend by day">
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Day</Table.Th>
            <Table.Th ta="right">Spend</Table.Th>
            <Table.Th ta="right">Tokens</Table.Th>
            <Table.Th ta="right">Requests</Table.Th>
            <Table.Th ta="right">Per 1M tokens</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {[...days].reverse().map((d) => (
            <Table.Tr key={d.date}>
              <Table.Td>{d.date}</Table.Td>
              <Table.Td ta="right">{money(d.cost)}</Table.Td>
              <Table.Td ta="right">{bigCount(d.tokens)}</Table.Td>
              <Table.Td ta="right">{integer(d.requests)}</Table.Td>
              <Table.Td ta="right">{d.cost_per_mtok === null ? DASH : money(d.cost_per_mtok)}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

function ChartCard({
  title,
  note,
  days,
  children,
}: {
  title: string;
  note: string;
  days: SpendDay[];
  children: ReactNode;
}) {
  const [view, setView] = useState('chart');
  return (
    <Card withBorder padding="md">
      <Group justify="space-between" align="flex-start" wrap="nowrap" mb="xs">
        <div style={{ minWidth: 0 }}>
          <Title order={5}>{title}</Title>
          <Text size="xs" c="dimmed">
            {note}
          </Text>
        </div>
        <SegmentedControl
          size="xs"
          style={{ flexShrink: 0 }}
          value={view}
          onChange={setView}
          data={[
            { value: 'chart', label: 'Chart' },
            { value: 'table', label: 'Table' },
          ]}
          aria-label={`${title}: chart or table`}
        />
      </Group>
      {view === 'table' ? <DailyTable days={days} /> : children}
    </Card>
  );
}

/**
 * Spend per day as columns and cost per million tokens as a line: two measures, so two charts, which share the
 * day under the pointer. While a new range loads the old charts stay, faded, rather than flashing a skeleton.
 */
export function DailyCharts({ days, refreshing }: { days: SpendDay[]; refreshing: boolean }) {
  const [active, setActive] = useState<Active | null>(null);
  const shared = { days, active, onActive: setActive };
  return (
    <SimpleGrid
      cols={{ base: 1, lg: 2 }}
      spacing="md"
      style={{ opacity: refreshing ? 0.55 : 1, transition: 'opacity 120ms' }}
    >
      <ChartCard title="Spend per day" note="USD of OpenRouter credits" days={days}>
        <SpendColumns {...shared} />
      </ChartCard>
      <ChartCard
        title="Cost per 1M tokens"
        note="Spend divided by tokens processed: lower is more efficient"
        days={days}
      >
        <EfficiencyLine {...shared} />
      </ChartCard>
    </SimpleGrid>
  );
}
