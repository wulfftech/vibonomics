/**
 * How much of shade 9 survives the mix with black, in percent. Shade 9 is the darkest the palette has and
 * still misses 4.5:1 on white for green, lime, yellow and orange, so those are pulled further; the rest keep
 * enough of their hue to stay recognisable. `tone.test.ts` holds every value to AA on a selected table row.
 */
const LIGHT_SHARE: Record<string, number> = { yellow: 65, lime: 70, green: 75, orange: 75 };
const DEFAULT_LIGHT_SHARE = 80;

/** Light-scheme text for a palette colour. Also the text of `light` badges and buttons, see `theme.ts`. */
export function toneLight(color: string): string {
  const share = LIGHT_SHARE[color] ?? DEFAULT_LIGHT_SHARE;
  return `color-mix(in srgb, var(--mantine-color-${color}-9) ${share}%, black)`;
}

/**
 * Text colours that adapt to the colour scheme. The stock `c="green"` style resolves to the
 * filled shade (contrast ~2.3:1 on white for green), so semantic text uses a dark shade in light
 * mode and a light shade in dark mode via CSS `light-dark()`. Mantine sets `color-scheme`.
 * Dark mode uses shade 3, not 4: shade 4 of violet, indigo and grape falls under 4.5:1 on a hovered row.
 */
export function tone(color: string): string {
  return `light-dark(${toneLight(color)}, var(--mantine-color-${color}-3))`;
}
