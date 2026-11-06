import {
  ActionIcon,
  Button,
  Card,
  Group,
  NumberInput,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title,
  Tooltip,
} from '@mantine/core';
import { Plus, X } from 'lucide-react';
import type { UsageProfile } from '../../api/types';
import { ErrorState, LoadingState } from '../../components/DataState';
import { SaveBar } from './SaveBar';
import { toNumber, useSettingsDraft } from './useSettingsDraft';

const WEIGHTS = [
  { key: 'intelligence_weight', label: 'Intelligence', description: 'General knowledge and reasoning leaderboards' },
  { key: 'coding_weight', label: 'Coding', description: 'Code generation and software-engineering benchmarks' },
  { key: 'agentic_weight', label: 'Agentic', description: 'Tool use and multi-step task benchmarks' },
  {
    key: 'own_tests_weight',
    label: 'Vibonomics tests',
    description:
      "Weight of Vibonomics's own test results inside coding and agentic, next to third-party metrics. It has no effect on a model whose own score is preferred (see below).",
  },
] as const;

const MIN_TASKS = 1;
const MAX_TASKS = 200;
const DEFAULT_MIN_TASKS = 5;

/** The server refuses anything outside 1..200, so a value typed or pasted outside it is pulled in here. */
function clampTasks(value: number): number {
  return Math.min(MAX_TASKS, Math.max(MIN_TASKS, Math.round(value)));
}

function slug(name: string, taken: Set<string>): string {
  const base =
    name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-|-$/g, '') || 'profile';
  let id = base;
  for (let n = 2; taken.has(id); n += 1) {
    id = `${base}-${n}`;
  }
  return id;
}

export function ScoringSection() {
  const { query, value, update, dirty, submit, saving, reset } = useSettingsDraft(['scoring']);

  if (query.isPending) {
    return <LoadingState />;
  }
  if (query.isError || value === null) {
    return <ErrorState message={query.error?.message} />;
  }
  const scoring = value.scoring;
  const profiles = scoring.profiles;

  const setProfile = (index: number, patch: Partial<UsageProfile>) =>
    update((old) => ({
      ...old,
      scoring: {
        ...old.scoring,
        profiles: old.scoring.profiles.map((p, i) => (i === index ? { ...p, ...patch } : p)),
      },
    }));

  const addProfile = () =>
    update((old) => {
      const taken = new Set(old.scoring.profiles.map((p) => p.id));
      return {
        ...old,
        scoring: {
          ...old.scoring,
          profiles: [
            ...old.scoring.profiles,
            {
              id: slug('custom', taken),
              name: 'Custom',
              input_weight: 3,
              output_weight: 1,
              cache_hit_rate: 0,
              task_input_tokens: 30000,
              task_output_tokens: 10000,
            },
          ],
        },
      };
    });

  const removeProfile = (index: number) =>
    update((old) => {
      const next = old.scoring.profiles.filter((_, i) => i !== index);
      const removed = old.scoring.profiles[index];
      return {
        ...old,
        scoring: {
          ...old.scoring,
          profiles: next,
          default_profile:
            removed?.id === old.scoring.default_profile ? (next[0]?.id ?? '') : old.scoring.default_profile,
        },
      };
    });

  const invalid =
    profiles.length === 0 || profiles.some((p) => p.name.trim() === '' || p.input_weight + p.output_weight <= 0);

  return (
    <Stack gap="lg">
      <Stack gap={4}>
        <Title order={3}>Scoring</Title>
        <Text size="sm" c="dimmed">
          How benchmark results become one score, and how prices are blended for comparison.
        </Text>
      </Stack>

      <Card withBorder padding="md">
        <Stack gap="xs">
          <Title order={4}>How scores work</Title>
          <Text size="sm" c="dimmed">
            Every third-party metric is converted to a percentile within its own leaderboard (0 = worst, 100 = best),
            which makes Elo ratings, pass rates and indices comparable. Each dimension (intelligence, coding, agentic)
            is the weighted mean of the percentiles available for a model; the composite is the weighted mean of the
            dimensions using the weights below. Value is the composite divided by the estimated cost of one task under
            the selected usage profile, shown as a 0–100 index on a log scale.
          </Text>
        </Stack>
      </Card>

      <Card withBorder padding="md">
        <Stack gap="sm">
          <Title order={4}>Weights</Title>
          <SimpleGrid cols={{ base: 1, sm: 2, md: 4 }} spacing="md">
            {WEIGHTS.map((weight) => (
              <NumberInput
                key={weight.key}
                label={weight.label}
                description={weight.description}
                min={0}
                step={0.05}
                decimalScale={3}
                value={scoring[weight.key]}
                onChange={(next) =>
                  update((old) => ({ ...old, scoring: { ...old.scoring, [weight.key]: toNumber(next) } }))
                }
              />
            ))}
          </SimpleGrid>
          <Text size="xs" c="dimmed">
            Weights are relative; they don&apos;t need to add up to 1.
          </Text>
        </Stack>
      </Card>

      <Card withBorder padding="md">
        <Stack gap="sm">
          <Title order={4}>Your own tests</Title>
          <Switch
            label="Prefer my own test scores"
            description="When a model has at least this many graded results from your own tests, its coding and agentic score IS its own test score instead of a blend with third-party benchmarks. Intelligence stays as computed."
            checked={scoring.prefer_own_tests}
            onChange={(event) => {
              const checked = event.currentTarget.checked;
              update((old) => ({ ...old, scoring: { ...old.scoring, prefer_own_tests: checked } }));
            }}
          />
          <NumberInput
            label="Fewest graded tasks"
            description="Graded task results, added up over providers. A model tested on only a few tasks is too noisy to override the benchmarks."
            min={MIN_TASKS}
            max={MAX_TASKS}
            allowDecimal={false}
            w={260}
            disabled={!scoring.prefer_own_tests}
            value={scoring.prefer_own_tests_min_tasks}
            onChange={(next) =>
              update((old) => ({
                ...old,
                scoring: { ...old.scoring, prefer_own_tests_min_tasks: clampTasks(toNumber(next, DEFAULT_MIN_TASKS)) },
              }))
            }
          />
        </Stack>
      </Card>

      <Card withBorder padding="md">
        <Stack gap="sm">
          <Title order={4}>Free models and value</Title>
          <Text size="sm" c="dimmed">
            A free model has no price, which used to make its value infinite and put every free model at the top of the
            value ranking. Value now costs free models at an assumed blended price so a weak free model cannot outrank a
            strong one just by being free.
          </Text>
          <NumberInput
            label="Assumed blended cost for free models"
            description="USD per 1M tokens, used only to calculate value. Set 0 to keep free models at infinite value."
            min={0}
            step={0.01}
            decimalScale={3}
            value={scoring.free_model_cost}
            onChange={(next) =>
              update((old) => ({ ...old, scoring: { ...old.scoring, free_model_cost: toNumber(next) } }))
            }
          />
        </Stack>
      </Card>

      <Card withBorder padding="md">
        <Stack gap="sm">
          <Group justify="space-between">
            <Title order={4}>Usage profiles</Title>
            <Button variant="light" size="xs" leftSection={<Plus size={14} />} onClick={addProfile}>
              Add profile
            </Button>
          </Group>
          <Text size="sm" c="dimmed">
            Blended $/1M = (input weight × effective input price + output weight × output price) ÷ (input weight +
            output weight), where the effective input price mixes in the cache-read price at the cache hit rate. A task
            is the token counts on the right; it drives $/task and value.
          </Text>
          <Table.ScrollContainer minWidth={820}>
            <Table verticalSpacing="xs">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Name</Table.Th>
                  <Table.Th w={110}>Input weight</Table.Th>
                  <Table.Th w={110}>Output weight</Table.Th>
                  <Table.Th w={110}>Cache hit %</Table.Th>
                  <Table.Th w={150}>Task input tokens</Table.Th>
                  <Table.Th w={150}>Task output tokens</Table.Th>
                  <Table.Th w={44} />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {profiles.map((profile, index) => (
                  <Table.Tr key={profile.id}>
                    <Table.Td>
                      <TextInput
                        size="xs"
                        aria-label="Profile name"
                        value={profile.name}
                        error={profile.name.trim() === ''}
                        onChange={(event) => setProfile(index, { name: event.currentTarget.value })}
                      />
                    </Table.Td>
                    <Table.Td>
                      <NumberInput
                        size="xs"
                        aria-label="Input weight"
                        min={0}
                        value={profile.input_weight}
                        onChange={(next) => setProfile(index, { input_weight: toNumber(next) })}
                      />
                    </Table.Td>
                    <Table.Td>
                      <NumberInput
                        size="xs"
                        aria-label="Output weight"
                        min={0}
                        value={profile.output_weight}
                        onChange={(next) => setProfile(index, { output_weight: toNumber(next) })}
                      />
                    </Table.Td>
                    <Table.Td>
                      <NumberInput
                        size="xs"
                        aria-label="Cache hit rate"
                        min={0}
                        max={100}
                        suffix="%"
                        value={Math.round(profile.cache_hit_rate * 1000) / 10}
                        onChange={(next) =>
                          setProfile(index, { cache_hit_rate: Math.min(1, Math.max(0, toNumber(next) / 100)) })
                        }
                      />
                    </Table.Td>
                    <Table.Td>
                      <NumberInput
                        size="xs"
                        aria-label="Task input tokens"
                        min={0}
                        step={1000}
                        thousandSeparator=","
                        value={profile.task_input_tokens}
                        onChange={(next) => setProfile(index, { task_input_tokens: Math.round(toNumber(next)) })}
                      />
                    </Table.Td>
                    <Table.Td>
                      <NumberInput
                        size="xs"
                        aria-label="Task output tokens"
                        min={0}
                        step={1000}
                        thousandSeparator=","
                        value={profile.task_output_tokens}
                        onChange={(next) => setProfile(index, { task_output_tokens: Math.round(toNumber(next)) })}
                      />
                    </Table.Td>
                    <Table.Td>
                      <Tooltip label={profiles.length <= 1 ? 'At least one profile is required' : 'Remove'}>
                        <ActionIcon
                          variant="subtle"
                          color="red"
                          aria-label={`Remove ${profile.name}`}
                          disabled={profiles.length <= 1}
                          onClick={() => removeProfile(index)}
                        >
                          <X size={16} />
                        </ActionIcon>
                      </Tooltip>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
          <Select
            label="Default profile"
            description="Used on the Models page until someone picks another one"
            w={260}
            data={profiles.map((p) => ({ value: p.id, label: p.name || p.id }))}
            value={profiles.some((p) => p.id === scoring.default_profile) ? scoring.default_profile : null}
            allowDeselect={false}
            onChange={(next) =>
              update((old) => ({
                ...old,
                scoring: { ...old.scoring, default_profile: next ?? old.scoring.default_profile },
              }))
            }
          />
        </Stack>
      </Card>

      <SaveBar dirty={dirty} canSave={!invalid} saving={saving} onReset={reset} onSave={() => submit()} />
    </Stack>
  );
}
