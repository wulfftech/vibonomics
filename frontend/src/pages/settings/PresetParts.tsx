import { Anchor, Badge, Button, Code, Collapse, Group, Stack, Table, Text } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { ChevronDown, ChevronRight, ExternalLink } from 'lucide-react';
import { Fragment, type ReactNode } from 'react';
import { CopyIconButton } from '../../components/CopyIconButton';
import type { PresetVersion } from '../../api/types';
import { DASH, dateTime, relativeTime } from '../../lib/format';
import { KEPT_AS_IT_IS, openRouterModelUrl } from '../../lib/presets';

/** A link that opens an OpenRouter page in a new tab without handing it the opener or the referrer. */
export function OpenRouterLink({
  href,
  children,
  label,
  size,
}: {
  href: string;
  children: ReactNode;
  label?: string;
  size?: string;
}) {
  return (
    <Anchor
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      inherit={size === undefined}
      size={size}
      aria-label={label}
    >
      {children}
      <ExternalLink size={12} style={{ marginInlineStart: 3, verticalAlign: '-1px' }} aria-hidden />
    </Anchor>
  );
}

/** "Open on OpenRouter" for a preset, or nothing when there is no page to open yet. */
export function PresetPageLink({ url }: { url: string | null }) {
  return url === null ? null : (
    <OpenRouterLink href={url} size="sm">
      Open on OpenRouter
    </OpenRouterLink>
  );
}

/** A model's name, or its id where there is no name, linked to its page on OpenRouter in a new tab. */
export function ModelLink({ id, name }: { id: string; name?: string }) {
  const label = name ?? id;
  return (
    <OpenRouterLink href={openRouterModelUrl(id)} label={`Open ${label} on OpenRouter`}>
      {label}
    </OpenRouterLink>
  );
}

/** Model links in a sentence or a cell, with a comma between them. */
export function ModelLinks({ ids, names }: { ids: string[]; names?: Map<string, string> }) {
  return ids.map((id, i) => (
    <Fragment key={id}>
      {i > 0 && ', '}
      <ModelLink id={id} name={names?.get(id)} />
    </Fragment>
  ));
}

/** A closed-by-default button that shows or hides the block under it, with the state a screen reader needs. */
export function Disclosure({
  label,
  children,
  extra,
}: {
  label: string;
  children: ReactNode;
  /** Shown beside the toggle while it is open, such as a copy button. */
  extra?: ReactNode;
}) {
  const [open, { toggle }] = useDisclosure(false);
  return (
    <Stack gap={4}>
      <Group gap="xs" justify="space-between">
        <Button
          variant="subtle"
          size="compact-sm"
          leftSection={open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          aria-expanded={open}
          onClick={toggle}
        >
          {label}
        </Button>
        {open && extra}
      </Group>
      <Collapse expanded={open}>{children}</Collapse>
    </Stack>
  );
}

/** Raw JSON in a block that scrolls, and can be scrolled from the keyboard. */
export function JsonBlock({ value, label }: { value: unknown; label: string }) {
  return (
    <Code block aria-label={label} tabIndex={0} style={{ maxHeight: 320, overflow: 'auto' }}>
      {JSON.stringify(value, null, 2)}
    </Code>
  );
}

/** The keys of a preset Vibonomics has no setting for, which it sends back as it found them. */
export function PassthroughBlock({ passthrough }: { passthrough: Record<string, unknown> }) {
  const empty = Object.keys(passthrough).length === 0;
  return (
    <Stack gap={4}>
      <Text fw={600} size="sm">
        Kept as it is
      </Text>
      <Text size="sm" c="dimmed">
        {KEPT_AS_IT_IS}
      </Text>
      {empty ? <Text size="sm">nothing</Text> : <JsonBlock value={passthrough} label="Kept as it is, raw JSON" />}
    </Stack>
  );
}

/** The raw config as OpenRouter holds it, behind a toggle, with a copy button. */
export function RawConfig({ config }: { config: Record<string, unknown> }) {
  return (
    <Disclosure
      label="Raw live config"
      extra={<CopyIconButton value={JSON.stringify(config, null, 2)} label="Copy the raw config" />}
    >
      <JsonBlock value={config} label="Raw live config" />
    </Disclosure>
  );
}

/** The versions of a preset on OpenRouter, newest first as OpenRouter lists them. Nothing for a preset with none. */
export function PresetVersions({
  versions,
  current,
}: {
  versions: PresetVersion[];
  current: number | null | undefined;
}) {
  if (versions.length === 0) {
    return null;
  }
  const live = versions.find((v) => v.live)?.version ?? current;
  const summary = live === null || live === undefined ? `${versions.length}` : `${versions.length}, live v${live}`;
  return (
    <Disclosure label={`Versions on OpenRouter (${summary})`}>
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
            {versions.map((v) => (
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
    </Disclosure>
  );
}
