import { Button, Group, PasswordInput, Tooltip } from '@mantine/core';
import { Lock } from 'lucide-react';
import type { ReactNode } from 'react';

/**
 * A secret that never shows its stored value. `value` is what the user typed: `null` means "keep
 * the stored value", `''` means "remove it" (the Remove button), anything else replaces it.
 */
export function SecretInput({
  label,
  description,
  stored,
  locked,
  envVar,
  value,
  onChange,
  required,
}: {
  label: ReactNode;
  description?: ReactNode;
  /** Whether a value is stored now. */
  stored: boolean;
  /** Set by an environment variable; not editable here. */
  locked?: boolean;
  envVar?: string;
  value: string | null;
  onChange: (value: string | null) => void;
  required?: boolean;
}) {
  const removing = value === '';
  const placeholder = locked
    ? 'Set by environment'
    : removing
      ? 'Will be removed on save'
      : stored
        ? 'Stored — type to replace'
        : 'Not set';

  const input = (
    <PasswordInput
      style={{ flex: 1 }}
      label={label}
      description={description}
      placeholder={placeholder}
      autoComplete="new-password"
      disabled={locked}
      required={required}
      value={value ?? ''}
      onChange={(event) => {
        const next = event.currentTarget.value;
        onChange(next === '' ? null : next);
      }}
      rightSection={
        locked ? (
          <Tooltip label={`Set by the environment variable ${envVar ?? ''}`.trim()}>
            <Lock size={14} aria-label="Locked" />
          </Tooltip>
        ) : undefined
      }
      visibilityToggleButtonProps={{ 'aria-label': 'Toggle visibility' }}
    />
  );

  if (locked || !stored) {
    return input;
  }

  return (
    <Group align="flex-end" gap="xs" wrap="nowrap">
      {input}
      <Button
        variant="light"
        color={removing ? 'gray' : 'red'}
        onClick={() => onChange(removing ? null : '')}
        aria-label={removing ? 'Keep the stored value' : 'Remove the stored value'}
      >
        {removing ? 'Keep' : 'Remove'}
      </Button>
    </Group>
  );
}
