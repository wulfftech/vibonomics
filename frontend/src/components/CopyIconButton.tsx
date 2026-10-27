import { ActionIcon, CopyButton, Tooltip } from '@mantine/core';
import { Check, Copy } from 'lucide-react';

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
  return (
    <CopyButton value={value} timeout={1500}>
      {({ copied, copy }) => (
        <Tooltip label={copied ? 'Copied' : label} withArrow>
          <ActionIcon
            variant="subtle"
            color={copied ? 'teal' : 'gray'}
            size={size}
            aria-label={label}
            onClick={(event) => {
              event.stopPropagation();
              copy();
            }}
          >
            {copied ? <Check size={14} /> : <Copy size={14} />}
          </ActionIcon>
        </Tooltip>
      )}
    </CopyButton>
  );
}
