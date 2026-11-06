import { MultiSelect, NumberInput, Stack, Switch, TagsInput, Text, Textarea } from '@mantine/core';
import { useState } from 'react';
import {
  cleanPatterns,
  IGNORE_LIMIT,
  NUMBER_LIMITS,
  PROMPT_LIMIT,
  QUANTIZATIONS,
  type NumberKey,
  type Quantization,
} from '../../lib/presets';

function display(value: number | '' | null): string {
  return value === null || value === '' ? '' : String(value);
}

/** The number a text reads as, or '' for text that is not one yet (empty, "-", "e"). */
function parse(text: string): number | '' {
  const trimmed = text.replace(/,/g, '').trim();
  if (trimmed === '') {
    return '';
  }
  const number = Number(trimmed);
  return Number.isFinite(number) ? number : '';
}

/**
 * A number the owner types. The text typed is kept for as long as it reads as the draft's value, so "0.50" and "-"
 * stay on screen while they are typed instead of being rewritten; any other value came from outside (Discard,
 * another preset) and replaces it. The draft gets a number, or '' while the text is not one.
 */
export function NumberField({
  name,
  label,
  value,
  disabled,
  placeholder,
  error,
  onChange,
}: {
  name: NumberKey;
  label: string;
  value: number | '' | null;
  disabled: boolean;
  placeholder: string;
  error: string | undefined;
  onChange: (value: number | '') => void;
}) {
  const [text, setText] = useState(() => display(value));
  const shown = parse(text) === value ? text : display(value);
  const { min, max, step, whole } = NUMBER_LIMITS[name];
  return (
    <NumberInput
      aria-label={label}
      value={shown}
      onChange={(next) => {
        const typed = String(next);
        setText(typed);
        onChange(parse(typed));
      }}
      disabled={disabled}
      placeholder={placeholder}
      error={error}
      min={min}
      max={max}
      step={step}
      // A value out of range stays as typed and is flagged, rather than being changed behind the owner's back.
      clampBehavior="none"
      allowNegative={min < 0}
      allowDecimal={whole !== true}
      decimalScale={whole === true ? 0 : 2}
      thousandSeparator={whole === true ? ',' : undefined}
    />
  );
}

export function QuantizationsField({
  label,
  value,
  disabled,
  placeholder,
  error,
  onChange,
}: {
  label: string;
  value: Quantization[] | null;
  disabled: boolean;
  placeholder: string;
  error: string | undefined;
  onChange: (value: Quantization[]) => void;
}) {
  return (
    <MultiSelect
      aria-label={label}
      data={[...QUANTIZATIONS]}
      value={value ?? []}
      onChange={onChange}
      disabled={disabled}
      placeholder={value === null || value.length === 0 ? placeholder : undefined}
      error={error}
      clearable
    />
  );
}

export function IgnoreField({
  label,
  value,
  disabled,
  placeholder,
  error,
  onChange,
}: {
  label: string;
  value: string[] | null;
  disabled: boolean;
  placeholder: string;
  error: string | undefined;
  onChange: (value: string[]) => void;
}) {
  return (
    <TagsInput
      aria-label={label}
      value={value ?? []}
      onChange={(next) => onChange(cleanPatterns(next))}
      disabled={disabled}
      placeholder={value === null || value.length === 0 ? placeholder : 'Add another'}
      error={error}
      maxTags={IGNORE_LIMIT}
      splitChars={[',', ' ']}
      clearable
    />
  );
}

export function RequireField({
  label,
  value,
  disabled,
  error,
  onChange,
}: {
  label: string;
  value: boolean | null;
  disabled: boolean;
  error: string | undefined;
  onChange: (value: boolean) => void;
}) {
  return (
    <Switch
      label={label}
      checked={value === true}
      disabled={disabled}
      error={error}
      onChange={(event) => onChange(event.currentTarget.checked)}
    />
  );
}

/** The prompt, in a box that grows with it, with how much of the 20,000 characters it uses. */
export function PromptField({
  label,
  value,
  disabled,
  placeholder,
  error,
  onChange,
}: {
  label: string;
  value: string | null;
  disabled: boolean;
  placeholder: string;
  error: string | undefined;
  onChange: (value: string) => void;
}) {
  return (
    <Stack gap={2}>
      <Textarea
        aria-label={label}
        value={value ?? ''}
        onChange={(event) => onChange(event.currentTarget.value)}
        disabled={disabled}
        placeholder={placeholder}
        error={error}
        autosize
        minRows={3}
        maxRows={14}
        maxLength={PROMPT_LIMIT}
      />
      {value !== null && (
        <Text size="xs" c="dimmed" ta="right">
          {value.length.toLocaleString('en')} / {PROMPT_LIMIT.toLocaleString('en')} characters
        </Text>
      )}
    </Stack>
  );
}
