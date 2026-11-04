import { Box, Card, Table, Text, Title, UnstyledButton } from '@mantine/core';
import type { ReactNode } from 'react';
import type { SpendBucket } from '../../api/types';
import { DASH, integer, money, percent } from '../../lib/format';
import { CHART, bigCount, shareLabel } from '../../lib/usage';

export interface BreakdownRow {
  id: string;
  name: string;
  /** Small print under the name. */
  note?: ReactNode;
  bucket: SpendBucket;
  /** Makes the name a button, for tables whose rows can narrow the whole page. */
  onPick?: () => void;
}

/** One row per model, key or provider: what it cost, and how efficiently. The bar compares spend within the table. */
export function BreakdownTable({
  title,
  description,
  nameHeader,
  rows,
  total,
}: {
  title: string;
  description?: string;
  nameHeader: string;
  rows: BreakdownRow[];
  total: number;
}) {
  const biggest = Math.max(0, ...rows.map((r) => r.bucket.cost));
  return (
    <Card withBorder padding="md">
      <Title order={5}>{title}</Title>
      {description && (
        <Text size="xs" c="dimmed" mb="xs">
          {description}
        </Text>
      )}
      <Table.ScrollContainer minWidth={640} type="native">
        <Table verticalSpacing={6} aria-label={title}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{nameHeader}</Table.Th>
              <Table.Th>Spend</Table.Th>
              <Table.Th ta="right">Share</Table.Th>
              <Table.Th ta="right">Tokens</Table.Th>
              <Table.Th ta="right">Requests</Table.Th>
              <Table.Th ta="right">Per 1M tokens</Table.Th>
              <Table.Th ta="right">Cached</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((row) => (
              <Table.Tr key={row.id}>
                <Table.Td maw={280}>
                  {row.onPick ? (
                    <UnstyledButton onClick={row.onPick} fz="sm" fw={500} c="var(--mantine-color-anchor)">
                      {row.name}
                    </UnstyledButton>
                  ) : (
                    <Text size="sm" fw={500} style={{ wordBreak: 'break-word' }}>
                      {row.name}
                    </Text>
                  )}
                  {row.note && (
                    <Text size="xs" c="dimmed">
                      {row.note}
                    </Text>
                  )}
                </Table.Td>
                <Table.Td miw={120}>
                  <Text size="sm">{money(row.bucket.cost)}</Text>
                  <Box
                    aria-hidden="true"
                    h={4}
                    mt={2}
                    style={{ borderRadius: 2, background: CHART.seriesTrack, overflow: 'hidden' }}
                  >
                    <Box
                      h={4}
                      style={{
                        width: `${biggest > 0 ? (row.bucket.cost / biggest) * 100 : 0}%`,
                        background: CHART.series,
                        borderRadius: 2,
                      }}
                    />
                  </Box>
                </Table.Td>
                <Table.Td ta="right">{shareLabel(row.bucket.cost, total)}</Table.Td>
                <Table.Td ta="right">{bigCount(row.bucket.tokens)}</Table.Td>
                <Table.Td ta="right">{integer(row.bucket.requests)}</Table.Td>
                <Table.Td ta="right">
                  {row.bucket.cost_per_mtok === null ? DASH : money(row.bucket.cost_per_mtok)}
                </Table.Td>
                <Table.Td ta="right">{row.bucket.cache_hit === null ? DASH : percent(row.bucket.cache_hit)}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Card>
  );
}
