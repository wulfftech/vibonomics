import { ActionIcon, Tooltip } from '@mantine/core';
import { Check, Copy, X } from 'lucide-react';
import { useCopyText } from '../lib/clipboard';

/** A small copy-to-clipboard icon button; clicks don't reach the row underneath. */
export function CopyIconButton({
  value,
  label = 'Copy',
  size = 'sm',
}: {
  value: string;
  label?: string;
  size?: string;
}) {
  const { copied, failed, copy } = useCopyText();
  return (
    <Tooltip label={copied ? 'Copied' : failed ? "Couldn't copy" : label} withArrow>
      <ActionIcon
        variant="subtle"
        color={copied ? 'teal' : failed ? 'red' : 'gray'}
        size={size}
        aria-label={label}
        onClick={(event) => {
          event.stopPropagation();
          void copy(value, event.currentTarget.parentElement ?? undefined);
        }}
      >
        {copied ? <Check size={14} /> : failed ? <X size={14} /> : <Copy size={14} />}
      </ActionIcon>
    </Tooltip>
  );
}
