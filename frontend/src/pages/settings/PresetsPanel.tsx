import { ActionIcon, Alert, Button, Card, Group, Stack, Text, TextInput, Title, Tooltip } from '@mantine/core';
import { Plus, RefreshCw, Search } from 'lucide-react';
import { useState } from 'react';
import { PresetEntryRow, RowButton } from './PresetRow';
import { LoadingState } from '../../components/DataState';
import { dateTime, relativeTime } from '../../lib/format';
import { GLOBAL_KEY, matchesSearch, type PresetEntry } from '../../lib/presets';
import type { PresetCatalog } from '../../api/types';

/** The list is searchable once it is longer than this. */
const SEARCH_FROM = 8;

/** What the panel needs to know about the catalogue query, whatever state it is in. */
export interface CatalogView {
  data: PresetCatalog | undefined;
  loading: boolean;
  /** Why the list could not be read at all; an old copy may still be on screen. */
  failure: string | null;
}

/**
 * The second vertical panel: the presets on the OpenRouter account, with a mark on the ones Vibonomics manages, a
 * Refresh, and a pinned entry for the settings they share. It only chooses; the page beside it shows the choice.
 */
export function PresetsPanel({
  entries,
  selected,
  onSelect,
  catalog,
  refreshing,
  onRefresh,
  onNew,
  canAdd,
  unsaved,
}: {
  entries: PresetEntry[];
  selected: string;
  onSelect: (key: string) => void;
  catalog: CatalogView;
  refreshing: boolean;
  onRefresh: () => void;
  onNew: () => void;
  canAdd: boolean;
  /** Slugs whose settings differ from what is saved. */
  unsaved: ReadonlySet<string>;
}) {
  const [search, setSearch] = useState('');
  const [dismissed, setDismissed] = useState<string | null>(null);
  const syncing = refreshing || catalog.data?.syncing === true;
  const synced = catalog.data?.synced_at;
  const problem = catalog.data?.error ?? null;
  const shown = entries.filter((entry) => matchesSearch(entry, search));

  return (
    <Card
      withBorder
      padding="sm"
      component="section"
      aria-label="Presets"
      w={{ base: '100%', md: 280 }}
      pos={{ base: 'static', md: 'sticky' }}
      top={{ md: 72 }}
      mah={{ base: 420, md: 'calc(100dvh - 96px)' }}
      style={{ flexShrink: 0, display: 'flex', flexDirection: 'column' }}
    >
      <Stack gap="xs" style={{ minHeight: 0, flex: 1 }}>
        <Group justify="space-between" wrap="nowrap">
          <Title order={5}>Presets</Title>
          <Group gap={4} wrap="nowrap">
            <Tooltip label="Refresh presets from OpenRouter" withArrow>
              <ActionIcon
                variant="subtle"
                color="gray"
                aria-label="Refresh presets from OpenRouter"
                loading={syncing}
                onClick={onRefresh}
              >
                <RefreshCw size={16} />
              </ActionIcon>
            </Tooltip>
            <Button
              size="compact-sm"
              leftSection={<Plus size={14} />}
              disabled={!canAdd}
              title={canAdd ? undefined : 'Vibonomics manages at most 20 presets'}
              onClick={onNew}
            >
              New preset
            </Button>
          </Group>
        </Group>
        <Text size="xs" c="dimmed" title={synced ? dateTime(synced) : undefined}>
          {synced ? `Synced ${relativeTime(synced)}` : 'Not synced yet'}
        </Text>
        {problem !== null && dismissed !== problem && (
          <Alert
            color="yellow"
            variant="light"
            p="xs"
            withCloseButton
            closeButtonLabel="Dismiss"
            onClose={() => setDismissed(problem)}
          >
            <Text size="xs">{problem}</Text>
          </Alert>
        )}
        {catalog.failure !== null && (
          <Alert color="red" variant="light" p="xs" title="Could not read the presets">
            <Text size="xs">{catalog.failure}</Text>
          </Alert>
        )}
        {entries.length > SEARCH_FROM && (
          <TextInput
            aria-label="Search presets"
            placeholder="Search presets"
            leftSection={<Search size={14} />}
            value={search}
            onChange={(event) => setSearch(event.currentTarget.value)}
          />
        )}
        <Stack gap={6} style={{ overflowY: 'auto', minHeight: 0, flex: 1 }}>
          <RowButton
            selected={selected === GLOBAL_KEY}
            onClick={() => onSelect(GLOBAL_KEY)}
            title="Global settings"
            detail="Blocklists shared by all presets"
          />
          {catalog.loading && <LoadingState label="Reading presets…" />}
          {shown.map((entry) => (
            <PresetEntryRow
              key={entry.slug}
              entry={entry}
              selected={selected === entry.slug}
              unsaved={unsaved.has(entry.slug)}
              onClick={() => onSelect(entry.slug)}
            />
          ))}
          {!catalog.loading && entries.length === 0 && (
            <Text size="sm" c="dimmed">
              No presets yet. Create one with New preset, or refresh to read your account.
            </Text>
          )}
          {entries.length > 0 && shown.length === 0 && (
            <Text size="sm" c="dimmed">
              Nothing matches &ldquo;{search.trim()}&rdquo;.
            </Text>
          )}
        </Stack>
      </Stack>
    </Card>
  );
}
