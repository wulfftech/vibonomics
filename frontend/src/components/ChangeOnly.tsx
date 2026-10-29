import { Tooltip } from '@mantine/core';
import type { ReactNode } from 'react';
import { useCanChange } from '../api/system';

/**
 * Wraps a control that changes something. A visitor to an instance that is open to read gets it disabled, with
 * the reason on hover; a disabled button cannot show a tooltip itself, so the wrapper does.
 */
export function ChangeOnly({ reason, children }: { reason: string; children: (blocked: boolean) => ReactNode }) {
  const canChange = useCanChange();
  return (
    <Tooltip label={reason} disabled={canChange} withArrow>
      <span style={{ display: 'inline-flex' }}>{children(!canChange)}</span>
    </Tooltip>
  );
}
