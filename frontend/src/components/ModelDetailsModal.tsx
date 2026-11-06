import {
  Anchor,
  Badge,
  Card,
  Code,
  Group,
  Modal,
  SimpleGrid,
  Spoiler,
  Stack,
  Table,
  Tabs,
  Text,
  Title,
  Tooltip,
} from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { ExternalLink } from 'lucide-react';
import type { ReactNode } from 'react';
import { useModelDetail } from '../api/models';
import type { ModelDetail, UsageProfile } from '../api/types';
import { compactTokens, dateOnly, dateTime, DASH, discountLabel, integer, money, relativeTime } from '../lib/format';
import { listPrices } from '../lib/modelTable';
import { clientName, scoreColor } from '../lib/modelText';
import { blendedPrice, profileFormula, taskCost } from '../lib/pricing';
import { isGraded, statusColor, statusLabel } from '../lib/status';
import { ModelBadges, ScoreNumber } from './models/cells';
import { CopyIconButton } from './CopyIconButton';
import { ProviderDot } from './ProviderDot';
import { providerTextColor } from '../lib/providerColors';
import { tone } from '../lib/tone';
import { EmptyState, ErrorState, LoadingState } from './DataState';
import { SnippetTabs } from './SnippetTabs';

const DIMENSION_COLORS: Record<string, string> = { intelligence: 'blue', coding: 'teal', agentic: 'grape' };

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Stack gap={0}>
      <Text size="xs" c="dimmed">
        {label}
      </Text>
      <Text size="sm" component="div">
        {children}
      </Text>
    </Stack>
  );
}

function BoolBadge({ value, label }: { value: boolean | null; label: string }) {
  if (value === null) {
    return (
      <Badge variant="light" color="gray" size="sm">
        {label}: unknown
      </Badge>
    );
  }
  return (
    <Badge variant="light" color={value ? 'green' : 'gray'} size="sm">
      {value ? label : `No ${label.toLowerCase()}`}
    </Badge>
  );
}

/** OpenCode's own docs pages for the terms below; the model lists differ between Console and Go. */
const TERMS_DOCS: Record<string, string> = {
  opencode: 'https://opencode.ai/v2/docs/console/models/',
  'opencode-go': 'https://opencode.ai/v2/docs/console/go/',
};

/** What the provider says about who may call a free model and what it does with your prompts. */
export function FreeTerms({ detail }: { detail: ModelDetail }) {
  const { row, data_use: dataUse } = detail;
  if (!row.client_restricted && !dataUse) {
    return null;
  }
  const docs = TERMS_DOCS[row.provider_type];
  return (
    <Card withBorder padding="md">
      <Stack gap="sm">
        <Title order={4}>Free model terms</Title>
        {row.client_restricted && (
          <Fact label="Who can call it">
            Only the {clientName(row.client_restricted)} client. Any other client gets HTTP 403, even with a valid API
            key, so Vibonomics cannot test it and every other harness on the Config tab says so. Paid models and Go work
            from other clients.
          </Fact>
        )}
        {dataUse && <Fact label="Your data">{dataUse}</Fact>}
        {docs && (
          <Text size="xs" c="dimmed">
            From{' '}
            <Anchor href={docs} target="_blank" rel="noreferrer" size="xs">
              OpenCode&apos;s model docs
            </Anchor>
            . Free periods are limited and the terms can change.
          </Text>
        )}
      </Stack>
    </Card>
  );
}

function OverviewTab({ detail, profile }: { detail: ModelDetail; profile: UsageProfile | undefined }) {
  const { row } = detail;
  const list = listPrices(row);
  const discounted = list !== null;
  const priceRows: [string, number | null | undefined, number | null | undefined][] = [
    ['Input', row.price.input, list?.input],
    ['Output', row.price.output, list?.output],
    ['Cache read', row.price.cache_read, list?.cache_read],
    ['Cache write', row.price.cache_write, discounted ? row.price.cache_write : undefined],
  ];
  return (
    <Stack gap="md">
      <FreeTerms detail={detail} />
      <Card withBorder padding="md">
        <Stack gap="sm">
          <Title order={4}>Prices per 1M tokens</Title>
          <Table verticalSpacing="xs" withRowBorders>
            <Table.Thead>
              <Table.Tr>
                <Table.Th />
                <Table.Th>{discounted ? 'Now' : 'Price'}</Table.Th>
                {discounted && <Table.Th>List</Table.Th>}
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {priceRows.map(([label, now, was]) => (
                <Table.Tr key={label}>
                  <Table.Td>{label}</Table.Td>
                  <Table.Td>{money(now)}</Table.Td>
                  {discounted && (
                    <Table.Td>
                      <Text size="sm" c="dimmed" td={was !== now ? 'line-through' : undefined}>
                        {money(was)}
                      </Text>
                    </Table.Td>
                  )}
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          {discounted && (
            <Group gap="xs">
              <Badge color="green" variant="light">
                {discountLabel(row.discount ?? 0)}
              </Badge>
              {row.discount_note && <Text size="sm">{row.discount_note}</Text>}
              {row.discount_until && (
                <Text size="sm" c="dimmed">
                  until {row.discount_until}
                </Text>
              )}
            </Group>
          )}
          {profile && (
            <Text size="sm">
              Blended ({profile.name}): <b>{row.is_free ? 'Free' : money(blendedPrice(row.price, profile))}</b> · est.{' '}
              {money(taskCost(row.price, profile, row.is_free))} per task
              <Text span size="xs" c="dimmed">
                {' '}
                — {profileFormula(profile)}
              </Text>
            </Text>
          )}
          <Text size="xs" c="dimmed">
            Pricing source: {row.pricing_source}
          </Text>
        </Stack>
      </Card>

      <Card withBorder padding="md">
        <Stack gap="sm">
          <Title order={4}>Model</Title>
          <Group gap="xs">
            <BoolBadge value={row.tool_call} label="Tool calling" />
            <BoolBadge value={row.reasoning} label="Reasoning" />
            <BoolBadge value={row.open_weights} label="Open weights" />
            {row.input_modalities.map((modality) => (
              <Badge key={modality} variant="light" color="teal" size="sm">
                {modality}
              </Badge>
            ))}
          </Group>
          <SimpleGrid cols={{ base: 2, sm: 3 }} spacing="sm">
            <Fact label="Context">{compactTokens(row.context_length)}</Fact>
            <Fact label="Max output">{compactTokens(row.max_output)}</Fact>
            <Fact label="Released">{dateOnly(row.release_date)}</Fact>
            <Fact label="First seen">
              <Tooltip label={dateTime(row.first_seen)}>
                <span>{relativeTime(row.first_seen)}</span>
              </Tooltip>
            </Fact>
            <Fact label="Creator">{row.creator ?? DASH}</Fact>
            <Fact label="Canonical model">{row.canonical_id ? <Code>{row.canonical_id}</Code> : DASH}</Fact>
          </SimpleGrid>
          {detail.description && (
            <Spoiler maxHeight={96} showLabel="Show more" hideLabel="Show less">
              <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
                {detail.description}
              </Text>
            </Spoiler>
          )}
        </Stack>
      </Card>
    </Stack>
  );
}

export function ScoreCard({ label, value, hint }: { label: string; value: number | null | undefined; hint?: string }) {
  return (
    <Card withBorder padding="sm">
      <Text size="xs" c="dimmed">
        {label}
      </Text>
      {value === null || value === undefined ? (
        <Text size="xl" fw={700} c="dimmed">
          {DASH}
        </Text>
      ) : (
        <Text size="xl" fw={700} style={{ color: tone(scoreColor(value)) }}>
          {Math.round(value)}
        </Text>
      )}
      {hint && (
        <Text size="xs" c="dimmed">
          {hint}
        </Text>
      )}
    </Card>
  );
}

function ScoresTab({ detail }: { detail: ModelDetail }) {
  const { scores } = detail.row;
  return (
    <Stack gap="md">
      <SimpleGrid cols={{ base: 2, sm: 5 }} spacing="sm">
        <ScoreCard label="Composite" value={scores.composite} hint={`${scores.source_count} sources`} />
        <ScoreCard label="Intelligence" value={scores.intelligence} />
        <ScoreCard label="Coding" value={scores.coding} />
        <ScoreCard label="Agentic" value={scores.agentic} />
        <ScoreCard label="Vibonomics tests" value={scores.own_test} />
      </SimpleGrid>

      {detail.metrics.length === 0 ? (
        <EmptyState message="No benchmark results matched this model." />
      ) : (
        <Card withBorder padding="md">
          <Table.ScrollContainer minWidth={640}>
            <Table verticalSpacing="xs">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Source</Table.Th>
                  <Table.Th>Metric</Table.Th>
                  <Table.Th>Value</Table.Th>
                  <Table.Th>Percentile</Table.Th>
                  <Table.Th>Effort</Table.Th>
                  <Table.Th>As of</Table.Th>
                  <Table.Th />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {detail.metrics.map((metric) => (
                  <Table.Tr key={`${metric.source}:${metric.metric}:${metric.effort}`}>
                    <Table.Td>
                      <Text size="sm">{metric.source_name}</Text>
                    </Table.Td>
                    <Table.Td>
                      <Group gap={6} wrap="nowrap">
                        <Text size="sm">{metric.label}</Text>
                        <Badge size="xs" variant="light" color={DIMENSION_COLORS[metric.dimension] ?? 'gray'}>
                          {metric.dimension}
                        </Badge>
                      </Group>
                    </Table.Td>
                    <Table.Td>{Number(metric.value.toFixed(2)).toLocaleString()}</Table.Td>
                    <Table.Td>
                      <ScoreNumber value={metric.percentile} />
                    </Table.Td>
                    <Table.Td>{metric.effort || DASH}</Table.Td>
                    <Table.Td>{dateOnly(metric.as_of)}</Table.Td>
                    <Table.Td>
                      {metric.url && (
                        <Anchor href={metric.url} target="_blank" rel="noreferrer" aria-label="Open the leaderboard">
                          <ExternalLink size={14} />
                        </Anchor>
                      )}
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </Card>
      )}

      {detail.row.reported_cost_per_task !== null && (
        <Text size="sm">
          Reported cost per task: <b>{money(detail.row.reported_cost_per_task)}</b>
          {detail.reported_cost_sources.length > 0 && (
            <Text span size="sm" c="dimmed">
              {' '}
              (median of {detail.reported_cost_sources.join(', ')})
            </Text>
          )}
        </Text>
      )}

      <Text size="sm" c="dimmed">
        Each third-party metric is converted to a percentile within its own leaderboard (0 = worst, 100 = best), so Elo
        ratings, pass rates and indices are comparable. Dimension scores are weighted means of those percentiles; the
        composite weights the dimensions as set in Settings › Scoring. Vibonomics&apos;s own test results feed coding
        and agentic.
      </Text>
      {detail.attributions.map((attribution) => (
        <Text key={attribution} size="xs" c="dimmed">
          {attribution}
        </Text>
      ))}
    </Stack>
  );
}

function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value !== '' ? value : null;
}

function uptime(value: unknown): string {
  const v = num(value);
  if (v === null) {
    return DASH;
  }
  return `${(v <= 1 ? v * 100 : v).toFixed(1)}%`;
}

function ProvidersTab({ detail, onChangeModel }: { detail: ModelDetail; onChangeModel: (id: number) => void }) {
  return (
    <Stack gap="md">
      <Card withBorder padding="md">
        <Stack gap="sm">
          <Title order={4}>Same model elsewhere</Title>
          {detail.other_offers.length === 0 ? (
            <Text size="sm" c="dimmed">
              {detail.row.canonical_id
                ? 'No other enabled provider offers this model.'
                : 'This model is not matched to a canonical model, so other offers cannot be found.'}
            </Text>
          ) : (
            <Table.ScrollContainer minWidth={520}>
              <Table verticalSpacing="xs" highlightOnHover>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Provider</Table.Th>
                    <Table.Th>Model ID</Table.Th>
                    <Table.Th>In / Out</Table.Th>
                    <Table.Th>Cache read</Table.Th>
                    <Table.Th />
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {detail.other_offers.map((offer) => (
                    <Table.Tr
                      key={offer.id}
                      style={{ cursor: 'pointer' }}
                      tabIndex={0}
                      onClick={() => onChangeModel(offer.id)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter') {
                          onChangeModel(offer.id);
                        }
                      }}
                    >
                      <Table.Td>{offer.provider_name}</Table.Td>
                      <Table.Td>
                        <Text ff="monospace" size="xs">
                          {offer.model_id}
                        </Text>
                      </Table.Td>
                      <Table.Td>
                        <Text size="sm">
                          {money(offer.price.input)} / {money(offer.price.output)}
                        </Text>
                      </Table.Td>
                      <Table.Td>{money(offer.price.cache_read)}</Table.Td>
                      <Table.Td>
                        <Group gap={4}>
                          {offer.is_free && (
                            <Badge size="xs" color="green" variant="light">
                              Free
                            </Badge>
                          )}
                          {(offer.discount ?? 0) > 0 && (
                            <Badge size="xs" color="green" variant="light">
                              {discountLabel(offer.discount ?? 0)}
                            </Badge>
                          )}
                        </Group>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          )}
        </Stack>
      </Card>

      {detail.endpoints.length > 0 && (
        <Card withBorder padding="md">
          <Stack gap="sm">
            <Title order={4}>OpenRouter endpoints</Title>
            <Text size="sm" c="dimmed">
              The upstream providers OpenRouter routes this model to, with their own prices.
            </Text>
            <Table.ScrollContainer minWidth={560}>
              <Table verticalSpacing="xs">
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Provider</Table.Th>
                    <Table.Th>In / Out</Table.Th>
                    <Table.Th>Discount</Table.Th>
                    <Table.Th>Quantization</Table.Th>
                    <Table.Th>Uptime (1 day)</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {detail.endpoints.map((endpoint, index) => {
                    const discount = num(endpoint.discount) ?? 0;
                    return (
                      <Table.Tr key={`${text(endpoint.provider) ?? ''}:${text(endpoint.tag) ?? index}`}>
                        <Table.Td>
                          <Text size="sm">{text(endpoint.provider) ?? DASH}</Text>
                          {text(endpoint.tag) && (
                            <Text size="xs" c="dimmed">
                              {text(endpoint.tag)}
                            </Text>
                          )}
                        </Table.Td>
                        <Table.Td>
                          {money(num(endpoint.input))} / {money(num(endpoint.output))}
                        </Table.Td>
                        <Table.Td>
                          {discount > 0 ? (
                            <Badge size="xs" color="green" variant="light">
                              {discountLabel(discount)}
                            </Badge>
                          ) : (
                            DASH
                          )}
                        </Table.Td>
                        <Table.Td>{text(endpoint.quantization) ?? DASH}</Table.Td>
                        <Table.Td>{uptime(endpoint.uptime_1d)}</Table.Td>
                      </Table.Tr>
                    );
                  })}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          </Stack>
        </Card>
      )}
    </Stack>
  );
}

function PriceHistoryTab({ detail }: { detail: ModelDetail }) {
  if (detail.price_history.length === 0) {
    return <EmptyState message="No price changes recorded yet." />;
  }
  return (
    <Card withBorder padding="md">
      <Table.ScrollContainer minWidth={480}>
        <Table verticalSpacing="xs">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Observed</Table.Th>
              <Table.Th>Input</Table.Th>
              <Table.Th>Output</Table.Th>
              <Table.Th>Cache read</Table.Th>
              <Table.Th>Discount</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {[...detail.price_history].reverse().map((point) => (
              <Table.Tr key={point.observed_at}>
                <Table.Td>{dateTime(point.observed_at)}</Table.Td>
                <Table.Td>{money(point.price_input)}</Table.Td>
                <Table.Td>{money(point.price_output)}</Table.Td>
                <Table.Td>{money(point.price_cache_read)}</Table.Td>
                <Table.Td>{(point.discount ?? 0) > 0 ? discountLabel(point.discount ?? 0) : DASH}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Card>
  );
}

export function TestsTab({ detail }: { detail: ModelDetail }) {
  const summary = detail.row.test;
  return (
    <Stack gap="md">
      {summary && (
        <SimpleGrid cols={{ base: 2, sm: 4 }} spacing="sm">
          <ScoreCard label="Average score" value={summary.score} hint={`${summary.tasks} tasks`} />
          <Card withBorder padding="sm">
            <Text size="xs" c="dimmed">
              Runs
            </Text>
            <Text size="xl" fw={700}>
              {summary.runs}
            </Text>
            <Text size="xs" c="dimmed">
              last {relativeTime(summary.last_run_at)}
            </Text>
          </Card>
          <Card withBorder padding="sm">
            <Text size="xs" c="dimmed">
              Cost per task
            </Text>
            <Text size="xl" fw={700}>
              {money(summary.cost_per_task)}
            </Text>
          </Card>
          <Card withBorder padding="sm">
            <Text size="xs" c="dimmed">
              Tokens per task
            </Text>
            <Text size="xl" fw={700}>
              {integer(summary.tokens_per_task)}
            </Text>
          </Card>
        </SimpleGrid>
      )}
      {detail.tests.length === 0 ? (
        <EmptyState message="Vibonomics hasn't tested this model yet. Select it on the Models page and use Test selected." />
      ) : (
        <Card withBorder padding="md">
          <Table.ScrollContainer minWidth={560}>
            <Table verticalSpacing="xs">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Run</Table.Th>
                  <Table.Th>Task</Table.Th>
                  <Table.Th>Status</Table.Th>
                  <Table.Th>Score</Table.Th>
                  <Table.Th>Judge notes</Table.Th>
                  <Table.Th>When</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {detail.tests.map((test) => (
                  <Table.Tr key={`${test.run_id}:${test.task_id}:${test.created_at}`}>
                    <Table.Td>#{test.run_id}</Table.Td>
                    <Table.Td>
                      <Text size="sm">{test.task_id}</Text>
                      <Text size="xs" c="dimmed">
                        {test.category}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Badge size="sm" variant="light" color={statusColor(test.status)}>
                        {statusLabel(test.status)}
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      <ScoreNumber value={isGraded(test.status) ? test.score : null} />
                    </Table.Td>
                    <Table.Td maw={280}>
                      {test.judge_notes ? (
                        <Tooltip label={test.judge_notes} multiline maw={400}>
                          <Text size="xs" lineClamp={2}>
                            {test.judge_notes}
                          </Text>
                        </Tooltip>
                      ) : (
                        DASH
                      )}
                    </Table.Td>
                    <Table.Td>{relativeTime(test.created_at)}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </Card>
      )}
    </Stack>
  );
}

/** Everything about one model offer. Clicking another provider's offer switches the modal to it. */
export function ModelDetailsModal({
  modelId,
  profile,
  onChangeModel,
  onClose,
}: {
  modelId: number | null;
  profile: UsageProfile | undefined;
  onChangeModel: (id: number) => void;
  onClose: () => void;
}) {
  const mobile = useMediaQuery('(max-width: 48em)');
  const detail = useModelDetail(modelId);
  const data = detail.data;

  return (
    <Modal opened={modelId !== null} onClose={onClose} title={data?.row.name ?? 'Model'} size="xl" fullScreen={mobile}>
      {detail.isPending && <LoadingState />}
      {detail.isError && <ErrorState message={detail.error.message} />}
      {data && (
        <Stack gap="md">
          <Group gap="xs" wrap="wrap">
            <Group gap={6} wrap="nowrap">
              <ProviderDot type={data.row.provider_type} size={9} />
              <Text size="sm" fw={500} style={{ color: providerTextColor(data.row.provider_type) }}>
                {data.row.provider_name}
              </Text>
            </Group>
            <Group gap={2} wrap="nowrap">
              <Code>{data.row.model_id}</Code>
              <CopyIconButton value={data.row.model_id} label="Copy model ID" />
            </Group>
            <ModelBadges row={data.row} />
          </Group>
          <Tabs defaultValue="overview" keepMounted={false}>
            <Tabs.List>
              <Tabs.Tab value="overview">Overview</Tabs.Tab>
              <Tabs.Tab value="scores">Scores</Tabs.Tab>
              <Tabs.Tab value="providers">Providers</Tabs.Tab>
              <Tabs.Tab value="history">Price history</Tabs.Tab>
              <Tabs.Tab value="tests">Tests</Tabs.Tab>
              <Tabs.Tab value="config">Config</Tabs.Tab>
            </Tabs.List>
            <Tabs.Panel value="overview" pt="md">
              <OverviewTab detail={data} profile={profile} />
            </Tabs.Panel>
            <Tabs.Panel value="scores" pt="md">
              <ScoresTab detail={data} />
            </Tabs.Panel>
            <Tabs.Panel value="providers" pt="md">
              <ProvidersTab detail={data} onChangeModel={onChangeModel} />
            </Tabs.Panel>
            <Tabs.Panel value="history" pt="md">
              <PriceHistoryTab detail={data} />
            </Tabs.Panel>
            <Tabs.Panel value="tests" pt="md">
              <TestsTab detail={data} />
            </Tabs.Panel>
            <Tabs.Panel value="config" pt="md">
              <SnippetTabs key={data.row.id} detail={data} />
            </Tabs.Panel>
          </Tabs>
        </Stack>
      )}
    </Modal>
  );
}
