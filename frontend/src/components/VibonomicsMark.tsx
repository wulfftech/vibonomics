import { Tooltip } from '@mantine/core';

const LABEL = 'Managed by Vibonomics';

/**
 * The Vibonomics logo (the paths of public/favicon.svg), to flag what Vibonomics keeps up to date. It is an image with
 * a name, so a row that holds it reads "Managed by Vibonomics" to a screen reader.
 */
export function VibonomicsMark({ size = 16 }: { size?: number }) {
  return (
    <Tooltip label={LABEL} withArrow>
      <svg
        role="img"
        aria-label={LABEL}
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 24 24"
        width={size}
        height={size}
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        style={{ flexShrink: 0, color: 'var(--mantine-color-anchor)' }}
      >
        <path d="M3 3v16a2 2 0 0 0 2 2h16" />
        <path d="m19 9-5 5-4-4-3 3" />
      </svg>
    </Tooltip>
  );
}
