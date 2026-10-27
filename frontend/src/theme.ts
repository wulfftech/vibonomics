import { Badge, createTheme, type CSSVariablesResolver } from '@mantine/core';

export const theme = createTheme({
  // Darker primary and dimmed text: the stock shades fail WCAG AA on this background.
  primaryShade: { light: 8, dark: 8 },
  primaryColor: 'indigo',
  fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
  fontFamilyMonospace: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
  defaultRadius: 'md',
  components: { Badge: Badge.extend({ styles: { root: { textTransform: 'none' } } }) },
});

/**
 * Contrast adjustments on top of Mantine's defaults. `dimmed` moves from gray-6/dark-2
 * (3.3:1 and 4.7:1) to values that clear WCAG AA for small text, and anchors follow the
 * primary colour at a readable shade in both schemes.
 */
export const cssVariablesResolver: CSSVariablesResolver = () => ({
  variables: {},
  light: {
    '--mantine-color-dimmed': '#6b7280',
    '--mantine-color-anchor': 'var(--mantine-color-indigo-8)',
  },
  dark: {
    '--mantine-color-dimmed': '#a6a6a6',
    '--mantine-color-anchor': 'var(--mantine-color-indigo-4)',
  },
});
