import { Select, Stack, Text, TextInput } from '@mantine/core';
import type { CatalogPreset } from '../../api/types';

const TEMPLATE_HELP = 'Its tools, reasoning and provider filters are copied into a new preset. Read only.';

/** What the pickers know of the account's presets: the catalogue, and why it may be missing. */
export interface TemplateSource {
  presets: CatalogPreset[];
  /** Why the presets cannot be listed; null when they can. */
  problem: string | null;
  loading: boolean;
}

/**
 * The account's own presets to start from, out of the catalogue. A preset Vibonomics manages is left out: it has its
 * own settings already. Falls back to typing a slug when the catalogue cannot be read.
 */
export function TemplatePicker({
  source,
  value,
  onChange,
}: {
  source: TemplateSource;
  value: string | null;
  onChange: (slug: string | null) => void;
}) {
  if (source.problem !== null) {
    return (
      <TextInput
        label="Start from preset (optional)"
        description={`${TEMPLATE_HELP} The list of your presets is unavailable: ${source.problem}`}
        placeholder="my-preset"
        value={value ?? ''}
        onChange={(event) => onChange(event.currentTarget.value)}
      />
    );
  }

  const presets = source.presets.filter((p) => !p.managed);
  const data = presets.map((p) => ({ value: p.slug, label: p.name === p.slug ? p.slug : `${p.name} (${p.slug})` }));
  if (value && !data.some((d) => d.value === value)) {
    data.unshift({ value, label: value });
  }
  const bySlug = new Map(presets.map((p) => [p.slug, p]));
  return (
    <Select
      label="Start from preset (optional)"
      description={TEMPLATE_HELP}
      placeholder={
        source.loading ? 'Loading your presets…' : presets.length > 0 ? 'None' : 'No other presets on your account'
      }
      searchable
      clearable
      data={data}
      value={value}
      onChange={onChange}
      renderOption={({ option }) => {
        const p = bySlug.get(option.value);
        return (
          <Stack gap={0}>
            <Text size="sm">{option.label}</Text>
            {p !== undefined && (
              <Text size="xs" c="dimmed">
                {[
                  p.models.length > 0 ? p.models.join(', ') : 'no model set',
                  p.providers.length > 0 ? `only ${p.providers.join(', ')}` : null,
                  p.version === null ? null : `v${p.version}`,
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </Text>
            )}
          </Stack>
        );
      }}
    />
  );
}
