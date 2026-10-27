/**
 * Text colours that adapt to the colour scheme. The stock `c="green"` style resolves to the
 * filled shade (contrast ~2.3:1 on white for green), so semantic text uses a dark shade in light
 * mode and a light shade in dark mode via CSS `light-dark()`. Mantine sets `color-scheme`.
 */
export function tone(color: string): string {
  return `light-dark(var(--mantine-color-${color}-8), var(--mantine-color-${color}-4))`;
}
