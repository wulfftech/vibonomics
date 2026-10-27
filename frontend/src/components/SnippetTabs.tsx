import { Anchor, Box, Code, Group, Stack, Tabs, Text } from '@mantine/core';
import { ExternalLink } from 'lucide-react';
import type { SnippetOut } from '../api/types';
import { CopyIconButton } from './CopyIconButton';
import { EmptyState } from './DataState';

/** One tab per harness (OpenCode, curl…) with the config to paste and where it goes. */
export function SnippetTabs({ snippets }: { snippets: SnippetOut[] }) {
  if (snippets.length === 0) {
    return <EmptyState message="No config snippets for this provider." />;
  }
  return (
    <Tabs defaultValue={snippets[0].id} keepMounted={false}>
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
