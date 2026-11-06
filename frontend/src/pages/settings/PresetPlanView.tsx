import { Alert, Anchor, Badge, Button, Code, Collapse, Group, Stack, Table, Text, Title } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { ChevronDown, ChevronRight, ExternalLink } from 'lucide-react';
import { Fragment } from 'react';
import { CopyIconButton } from '../../components/CopyIconButton';
import { DASH, dateTime, discountLabel, money, relativeTime } from '../../lib/format';
import { openRouterModelUrl } from '../../lib/presets';
import type { PresetPlan } from '../../api/types';

const ACTIONS: Record<PresetPlan['action'], { label: string; color: string }> = {
  create: { label: 'Create', color: 'teal' },
  update: { label: 'Update', color: 'blue' },
  unchanged: { label: 'No change', color: 'gray' },
  blocked: { label: 'Blocked', color: 'orange' },
};

/** The value of a changed key, which arrives as JSON text; a key that is new or gone shows as nothing. */
function shown(json: string | null | undefined): string {
  return json === null || json === undefined ? DASH : json;
}

/** A model's name, or its id where there is no name, linked to its page on OpenRouter in a new tab. */
function ModelLink({ id, name }: { id: string; name?: string }) {
  const label = name ?? id;
  return (
    <Anchor
      href={openRouterModelUrl(id)}
      target="_blank"
      rel="noopener noreferrer"
      inherit
      aria-label={`Open ${label} on OpenRouter`}
    >
      {label}
      <ExternalLink size={12} style={{ marginInlineStart: 3, verticalAlign: '-1px' }} />
    </Anchor>
  );
}

/** Model links in a sentence or a cell, with a comma between them. */
function ModelLinks({ ids, names }: { ids: string[]; names: Map<string, string> }) {
  return ids.map((id, i) => (
    <Fragment key={id}>
      {i > 0 && ', '}
      <ModelLink id={id} name={names.get(id)} />
    </Fragment>
  ));
}

function Versions({ plan }: { plan: PresetPlan }) {
  const [open, { toggle }] = useDisclosure(false);
  if (plan.versions.length === 0) {
    return null;
  }
  const live = plan.versions.find((v) => v.live)?.version ?? plan.current_version;
  const summary =
    live === null || live === undefined ? `${plan.versions.length}` : `${plan.versions.length}, live v${live}`;
  return (
    <Stack gap={4}>
      <Group>
        <Button
          variant="subtle"
          size="compact-sm"
          leftSection={open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          aria-expanded={open}
          onClick={toggle}
        >
          {`Versions on OpenRouter (${summary})`}
        </Button>
      </Group>
      <Collapse expanded={open}>
        <Table.ScrollContainer minWidth={420}>
          <Table verticalSpacing={4} withRowBorders={false} aria-label="Preset versions">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Version</Table.Th>
                <Table.Th>Saved</Table.Th>
                <Table.Th>Model</Table.Th>
                <Table.Th>Providers</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {plan.versions.map((v) => (
                <Table.Tr key={v.version ?? 'unknown'}>
                  <Table.Td>
                    <Group gap={6} wrap="nowrap">
                      <Text size="sm">v{v.version ?? DASH}</Text>
                      {v.live && (
                        <Badge size="xs" variant="light" color="teal">
                          live
                        </Badge>
                      )}
                    </Group>
                  </Table.Td>
                  <Table.Td title={dateTime(v.created_at)}>
                    <Text size="sm">{relativeTime(v.created_at)}</Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm">{v.model ? <ModelLink id={v.model} /> : DASH}</Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm">{v.providers.join(', ') || DASH}</Text>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Collapse>
    </Stack>
  );
}

/** What the next update would do, and the exact request that would do it. */
export function PresetPlanView({ plan }: { plan: PresetPlan }) {
  const action = ACTIONS[plan.action];
  const body = plan.request ? JSON.stringify(plan.request.body, null, 2) : '';
  const [open, { toggle }] = useDisclosure(false);
  const names = new Map(plan.picks.map((c) => [c.model_id, c.name]));
  return (
    <Stack gap="md">
      <Group gap="sm" wrap="nowrap" align="flex-start">
        <Badge color={action.color} variant="filled" style={{ flexShrink: 0 }}>
          {action.label}
        </Badge>
        <Text size="sm">{plan.reason}</Text>
        {plan.draft && (
          <Badge color="gray" variant="light" style={{ flexShrink: 0 }}>
            Unsaved settings
          </Badge>
        )}
      </Group>

      <Group gap="xl">
        <Stack gap={0}>
          <Text size="xs" c="dimmed">
            Live version
          </Text>
          <Text fw={600}>{plan.current_version === null ? 'Not created yet' : `v${plan.current_version}`}</Text>
        </Stack>
        <Stack gap={0}>
          <Text size="xs" c="dimmed">
            Preset
          </Text>
          <Text fw={600}>@preset/{plan.slug}</Text>
        </Stack>
        <Stack gap={0}>
          <Text size="xs" c="dimmed">
            Models passing the filters
          </Text>
          <Text fw={600}>{plan.considered}</Text>
        </Stack>
      </Group>

      {plan.warnings.map((warning) => (
        <Alert key={warning} color="yellow" variant="light" py="xs">
          {warning}
        </Alert>
      ))}

      {plan.picks.length === 1 && plan.pick && (
        <Stack gap={4}>
          <Text fw={600} size="sm">
            {plan.kept_current ? 'Staying on' : 'Best value'}
          </Text>
          <Text size="sm">
            <ModelLink id={plan.pick.model_id} name={plan.pick.name} /> <Code>{plan.pick.model_id}</Code> scores{' '}
            {plan.pick.score}, about {money(plan.pick.task_cost)} a task, {Math.round(plan.pick.value).toLocaleString()}{' '}
            points per dollar.
          </Text>
        </Stack>
      )}

      {plan.picks.length > 1 && (
        <Stack gap={4}>
          <Text fw={600} size="sm">
            {plan.kept_current ? 'Models, kept steady' : 'Models'}, in the order OpenRouter tries them
          </Text>
          <Table.ScrollContainer minWidth={460}>
            <Table verticalSpacing={4} withRowBorders={false} aria-label="Models">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>#</Table.Th>
                  <Table.Th>Model</Table.Th>
                  <Table.Th>Score</Table.Th>
                  <Table.Th>Per task</Table.Th>
                  <Table.Th>Value</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {plan.picks.map((c, i) => (
                  <Table.Tr key={c.model_id}>
                    <Table.Td>{i + 1}</Table.Td>
                    <Table.Td>
                      <ModelLink id={c.model_id} name={c.name} /> <Code>{c.model_id}</Code>
                    </Table.Td>
                    <Table.Td>{c.score}</Table.Td>
                    <Table.Td>{money(c.task_cost)}</Table.Td>
                    <Table.Td>{Math.round(c.value).toLocaleString()}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </Stack>
      )}

      {plan.providers.length > 0 && (
        <Stack gap={4}>
          <Text fw={600} size="sm">
            Providers, cheapest first{plan.picks.length > 1 ? ', taken in turn from each model' : ''}
          </Text>
          <Table.ScrollContainer minWidth={460}>
            <Table verticalSpacing={4} withRowBorders={false} aria-label="Providers">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Provider</Table.Th>
                  <Table.Th>Slug</Table.Th>
                  <Table.Th>In / out per 1M</Table.Th>
                  <Table.Th>Per task</Table.Th>
                  {plan.picks.length > 1 && <Table.Th>For</Table.Th>}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {plan.providers.map((p) => (
                  <Table.Tr key={p.tag}>
                    <Table.Td>{p.provider ?? DASH}</Table.Td>
                    <Table.Td>
                      <Code>{p.tag}</Code>
                    </Table.Td>
                    <Table.Td>
                      {money(p.input)} / {money(p.output)}
                      {p.discount > 0 ? ` (${discountLabel(p.discount)})` : ''}
                    </Table.Td>
                    <Table.Td>{money(p.task_cost)}</Table.Td>
                    {plan.picks.length > 1 && (
                      <Table.Td>
                        <Text size="sm">
                          <ModelLinks ids={p.models} names={names} />
                        </Text>
                      </Table.Td>
                    )}
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </Stack>
      )}

      {plan.changes.length > 0 && (
        <Stack gap={4}>
          <Text fw={600} size="sm">
            What changes
          </Text>
          <Table.ScrollContainer minWidth={420}>
            <Table verticalSpacing={4} withRowBorders={false} aria-label="Changes">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Setting</Table.Th>
                  <Table.Th>Now</Table.Th>
                  <Table.Th>After</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {plan.changes.map((c) => (
                  <Table.Tr key={c.path}>
                    <Table.Td>
                      <Code>{c.path}</Code>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm" style={{ wordBreak: 'break-word' }}>
                        {shown(c.before)}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm" style={{ wordBreak: 'break-word' }}>
                        {shown(c.after)}
                      </Text>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </Stack>
      )}

      {plan.request && (
        <Stack gap={4}>
          <Group gap="xs" justify="space-between">
            <Button
              variant="subtle"
              size="compact-sm"
              leftSection={open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              aria-expanded={open}
              onClick={toggle}
            >
              {open ? 'Hide API call' : 'View API call'}
            </Button>
            {open && <CopyIconButton value={body} label="Copy the request body" />}
          </Group>
          <Collapse expanded={open}>
            <Stack gap={4}>
              <Text size="xs" c="dimmed">
                The request exactly as it would be sent:{' '}
                <Code>
                  {plan.request.method} {plan.request.url}
                </Code>{' '}
                with your OpenRouter key. OpenRouter requires <Code>messages</Code> and then ignores it.
              </Text>
              <Code block aria-label="Request body">
                {body}
              </Code>
            </Stack>
          </Collapse>
        </Stack>
      )}

      <Versions plan={plan} />

      {plan.runners_up.length > 0 && (
        <Stack gap={4}>
          <Title order={6}>Next best by value</Title>
          <Text size="sm" c="dimmed">
            {plan.runners_up.map((c, i) => (
              <Fragment key={c.model_id}>
                {i > 0 && ', '}
                <ModelLink id={c.model_id} name={c.name} /> ({Math.round(c.value).toLocaleString()})
              </Fragment>
            ))}
          </Text>
        </Stack>
      )}
    </Stack>
  );
}
