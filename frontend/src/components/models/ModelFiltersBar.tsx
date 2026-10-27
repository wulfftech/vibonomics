import { Chip, Group, MultiSelect, Select, Stack, Text, TextInput, Tooltip } from '@mantine/core';
import { CircleQuestionMark, Search } from 'lucide-react';
import type { UsageProfile } from '../../api/types';
import { compactTokens } from '../../lib/format';
import { GROUP_OPTIONS, QUICK_FILTERS, type ModelFilters } from '../../lib/modelTable';
import { profileFormula } from '../../lib/pricing';

const CONTEXT_OPTIONS = [
  { value: '0', label: 'Any' },
  { value: '32000', label: '32k' },
  { value: '128000', label: '128k' },
  { value: '200000', label: '200k' },
  { value: '1000000', label: '1M' },
];

function ProfileHelp({ profile }: { profile: UsageProfile | undefined }) {
  return (
    <Tooltip
      multiline
      maw={340}
      withArrow
      label={
        <Stack gap={4}>
          <Text size="xs">
            The blended price folds input, output and cached-input prices into one $/1M figure using the profile&apos;s
            input:output weights and cache hit rate. $/task and value use the profile&apos;s typical task size.
          </Text>
          {profile && (
            <Text size="xs">
              {profile.name}: {profileFormula(profile)}; a task is {compactTokens(profile.task_input_tokens)} input +{' '}
              {compactTokens(profile.task_output_tokens)} output tokens.
            </Text>
          )}
        </Stack>
      }
    >
      <CircleQuestionMark
        size={13}
        style={{ verticalAlign: 'middle', cursor: 'help' }}
        aria-label="About usage profiles"
      />
    </Tooltip>
  );
}

export function ModelFiltersBar({
  filters,
  onChange,
  profiles,
  profile,
  providers,
}: {
  filters: ModelFilters;
  onChange: (update: (old: ModelFilters) => ModelFilters) => void;
  profiles: UsageProfile[];
  profile: UsageProfile | undefined;
  providers: { value: string; label: string }[];
}) {
  return (
    <Stack gap="sm">
      <Group align="flex-end" gap="sm">
        <TextInput
          label="Search"
          placeholder="Name, id, provider, creator"
          leftSection={<Search size={14} />}
          w={220}
          value={filters.search}
          onChange={(event) => {
            const search = event.currentTarget.value;
            onChange((old) => ({ ...old, search }));
          }}
        />
        <MultiSelect
          label="Providers"
          placeholder={filters.providers.length === 0 ? 'All' : undefined}
          data={providers}
          w={220}
          clearable
          searchable
          value={filters.providers}
          onChange={(value) => onChange((old) => ({ ...old, providers: value }))}
        />
        <Select
          label="Group by"
          data={GROUP_OPTIONS.map((option) => ({ ...option }))}
          w={160}
          allowDeselect={false}
          value={filters.groupBy}
          onChange={(value) => onChange((old) => ({ ...old, groupBy: value ?? 'none' }))}
        />
        <Select
          label={
            <Group gap={4} wrap="nowrap" component="span">
              Usage profile <ProfileHelp profile={profile} />
            </Group>
          }
          data={profiles.map((p) => ({ value: p.id, label: p.name }))}
          w={200}
          allowDeselect={false}
          value={profile?.id ?? null}
          onChange={(value) => onChange((old) => ({ ...old, profileId: value }))}
        />
        <Select
          label="Min context"
          data={CONTEXT_OPTIONS}
          w={140}
          allowDeselect={false}
          value={String(filters.minContext)}
          onChange={(value) => onChange((old) => ({ ...old, minContext: Number(value ?? 0) }))}
        />
      </Group>
      <Chip.Group multiple value={filters.chips} onChange={(value) => onChange((old) => ({ ...old, chips: value }))}>
        <Group gap="xs">
          {QUICK_FILTERS.map((chip) => (
            <Chip key={chip.value} value={chip.value} size="xs" variant="light">
              {chip.label}
            </Chip>
          ))}
        </Group>
      </Chip.Group>
    </Stack>
  );
}
