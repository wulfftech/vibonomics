import { TagsInput } from '@mantine/core';
import { cleanPatterns } from '../../lib/presets';

/**
 * A list of patterns, one tag each: Enter or a comma adds one, and Backspace in the empty field takes the last away,
 * which is how the keyboard removes a tag (the tag's own close button is not a tab stop).
 */
export function BlocklistField({
  label,
  description,
  placeholder,
  value,
  onChange,
}: {
  label: string;
  description: string;
  placeholder: string;
  value: string[];
  onChange: (patterns: string[]) => void;
}) {
  return (
    <TagsInput
      label={label}
      description={description}
      placeholder={value.length === 0 ? placeholder : undefined}
      value={value}
      onChange={(next) => onChange(cleanPatterns(next))}
      clearable
    />
  );
}
