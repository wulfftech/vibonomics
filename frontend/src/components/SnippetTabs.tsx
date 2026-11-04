import { Anchor, Box, Checkbox, Code, Group, Select, Stack, Tabs, Text } from '@mantine/core';
import { ExternalLink } from 'lucide-react';
import { useMemo, useState } from 'react';
import { useModelSnippets } from '../api/models';
import type { ModelDetail, SnippetOut } from '../api/types';
import {
  DEFAULT_ROUTE,
  initialRoute,
  pinnedTag,
  routeEndpoints,
  routeExplanation,
  routeGroups,
  routeQuery,
} from '../lib/routing';
import { CopyIconButton } from './CopyIconButton';
import { EmptyState, ErrorState, LoadingState } from './DataState';

/**
 * One tab per harness (OpenCode, curl…) with the config to paste and where it goes. An OpenRouter model also
 * gets a "Route via" choice: its cheapest or fastest provider, or one provider pinned.
 */
export function SnippetTabs({ detail }: { detail: ModelDetail }) {
  const { row } = detail;
  const endpoints = useMemo(() => routeEndpoints(detail.endpoints), [detail.endpoints]);
  const routable = row.provider_type === 'openrouter';
  const [route, setRoute] = useState(() => (routable ? initialRoute(row, endpoints) : DEFAULT_ROUTE));
  const [fallbacks, setFallbacks] = useState(false);
  const query = routable ? routeQuery(route, fallbacks) : null;
  const routed = useModelSnippets(row.id, query);

  let body;
  if (query === null) {
    body = <SnippetPanels snippets={detail.snippets} />;
  } else if (routed.isError) {
    body = <ErrorState message={routed.error.message} />;
  } else if (routed.data === undefined) {
    body = <LoadingState />;
  } else {
    body = (
      <Box style={{ opacity: routed.isFetching ? 0.5 : 1 }} aria-busy={routed.isFetching}>
        <SnippetPanels snippets={routed.data} />
      </Box>
    );
  }

  return (
    <Stack gap="md">
      {routable && (
        <Stack gap={6}>
          <Select
            label="Route via"
            data={routeGroups(row.model_id, endpoints)}
            value={route}
            onChange={(value) => value !== null && setRoute(value)}
            allowDeselect={false}
            searchable
            comboboxProps={{ withinPortal: true }}
          />
          {pinnedTag(route) !== null && (
            <Checkbox
              label="Allow fallbacks to other providers"
              checked={fallbacks}
              onChange={(event) => setFallbacks(event.currentTarget.checked)}
            />
          )}
          <Text size="xs" c="dimmed">
            {routeExplanation(route, fallbacks)}
          </Text>
        </Stack>
      )}
      {body}
    </Stack>
  );
}

function SnippetPanels({ snippets }: { snippets: SnippetOut[] }) {
  const [tab, setTab] = useState<string | null>(null);
  if (snippets.length === 0) {
    return <EmptyState message="No config snippets for this provider." />;
  }
  // A new route can drop a tab (Aider's second file); fall back to the first rather than show nothing.
  const active = snippets.some((snippet) => snippet.id === tab) ? tab : snippets[0].id;
  return (
    <Tabs value={active} onChange={setTab} keepMounted={false}>
      <Tabs.List>
        {snippets.map((snippet) => (
          <Tabs.Tab key={snippet.id} value={snippet.id}>
            {snippet.harness}
          </Tabs.Tab>
        ))}
      </Tabs.List>
      {snippets.map((snippet) => (
        <Tabs.Panel key={snippet.id} value={snippet.id} pt="md">
          <Stack gap="sm">
            {snippet.notes && (
              <Text size="sm" c="dimmed">
                {snippet.notes}
              </Text>
            )}
            <Group justify="space-between" wrap="nowrap">
              <Text size="sm" fw={500}>
                {snippet.filename ? <Code>{snippet.filename}</Code> : snippet.title}
              </Text>
              {snippet.docs_url && (
                <Anchor href={snippet.docs_url} target="_blank" rel="noreferrer" size="sm">
                  <Group gap={4} wrap="nowrap">
                    Docs <ExternalLink size={12} />
                  </Group>
                </Anchor>
              )}
            </Group>
            <Box pos="relative">
              <Code block style={{ whiteSpace: 'pre', overflowX: 'auto', paddingRight: 40 }}>
                {snippet.content}
              </Code>
              <Box pos="absolute" top={6} right={6}>
                <CopyIconButton value={snippet.content} label={`Copy ${snippet.filename ?? snippet.title}`} />
              </Box>
            </Box>
            <Text size="xs" c="dimmed">
              Snippets never contain your API key; set the environment variable they reference.
            </Text>
          </Stack>
        </Tabs.Panel>
      ))}
    </Tabs>
  );
}
