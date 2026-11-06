import { DEFAULT_THEME, darken, mergeMantineTheme } from '@mantine/core';
import { describe, expect, it } from 'vitest';
import { cssVariablesResolver, theme } from '../theme';
import { blendedColor } from './pricing';
import { providerColor, providerTextColor } from './providerColors';
import { tone } from './tone';

type Scheme = 'light' | 'dark';
type Rgb = [number, number, number];

const PALETTE = DEFAULT_THEME.colors;
const COLORS = Object.keys(PALETTE).filter((name) => name !== 'dark');
const AA = 4.5;

function fromHex(hex: string): Rgb {
  let digits = hex.replace('#', '');
  if (digits.length === 3) {
    digits = [...digits].map((digit) => digit + digit).join('');
  }
  return [0, 2, 4].map((offset) => parseInt(digits.slice(offset, offset + 2), 16) / 255) as Rgb;
}

/** Splits a function's arguments on the commas that are not inside a nested call. */
function splitArguments(text: string): string[] {
  const parts: string[] = [];
  let depth = 0;
  let start = 0;
  for (let index = 0; index < text.length; index += 1) {
    if (text[index] === '(') {
      depth += 1;
    } else if (text[index] === ')') {
      depth -= 1;
    } else if (text[index] === ',' && depth === 0) {
      parts.push(text.slice(start, index).trim());
      start = index + 1;
    }
  }
  parts.push(text.slice(start).trim());
  return parts;
}

/** Resolves the CSS colour forms this app emits: light-dark(), color-mix(in srgb, X p%, black), a palette var(). */
function resolve(css: string, scheme: Scheme): Rgb {
  const text = css.trim();
  const call = /^([a-z-]+)\(([\s\S]*)\)$/.exec(text);
  if (call === null) {
    throw new Error(`Cannot resolve the colour "${css}"`);
  }
  const [, name, inner] = call;
  const args = splitArguments(inner);
  if (name === 'light-dark') {
    return resolve(args[scheme === 'light' ? 0 : 1], scheme);
  }
  if (name === 'var') {
    const palette = /^--mantine-color-([a-z]+)-(\d)$/.exec(args[0]);
    if (palette === null || PALETTE[palette[1]] === undefined) {
      throw new Error(`Cannot resolve the variable "${args[0]}"`);
    }
    return fromHex(PALETTE[palette[1]][Number(palette[2])]);
  }
  if (name === 'color-mix') {
    const share = /^([\s\S]+)\s+(\d+(?:\.\d+)?)%$/.exec(args[1]);
    if (args[0] !== 'in srgb' || args[2] !== 'black' || share === null) {
      throw new Error(`Only "color-mix(in srgb, X p%, black)" is supported, got "${css}"`);
    }
    return resolve(share[1], scheme).map((channel) => (channel * Number(share[2])) / 100) as Rgb;
  }
  throw new Error(`Cannot resolve the colour "${css}"`);
}

function luminance(color: Rgb): number {
  const [red, green, blue] = color.map((channel) =>
    channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4,
  );
  return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
}

function contrast(a: Rgb, b: Rgb): number {
  const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (light + 0.05) / (dark + 0.05);
}

function fromRgba(css: string): Rgb {
  const channels = /rgba?\(([^)]+)\)/.exec(css)?.[1].split(',') ?? [];
  return channels.slice(0, 3).map((channel) => Number(channel) / 255) as Rgb;
}

/** The backgrounds text sits on: the page and every card and modal (`body`), a hovered row, a selected row. */
const BACKGROUNDS: Record<Scheme, Record<string, Rgb>> = {
  light: {
    page: fromHex(DEFAULT_THEME.white),
    'hovered row': fromHex(PALETTE.gray[0]),
    'selected row': fromHex(PALETTE.indigo[1]),
  },
  dark: {
    page: fromHex(PALETTE.dark[7]),
    'hovered row': fromHex(PALETTE.dark[5]),
    'selected row': fromRgba(darken(PALETTE.indigo[9], 0.5)),
  },
};

/** Every (label, CSS colour) pair that falls under AA on one of the backgrounds, as readable lines. */
function failures(entries: Array<[string, string]>): string[] {
  const found: string[] = [];
  for (const scheme of ['light', 'dark'] as const) {
    for (const [label, css] of entries) {
      const text = resolve(css, scheme);
      for (const [where, background] of Object.entries(BACKGROUNDS[scheme])) {
        const ratio = contrast(text, background);
        if (ratio < AA) {
          found.push(`${label} in ${scheme} mode on the ${where}: ${ratio.toFixed(2)}`);
        }
      }
    }
  }
  return found;
}

describe('tone', () => {
  it('reaches WCAG AA for every palette colour on the page, a hovered row and a selected row, in both schemes', () => {
    expect(failures(COLORS.map((color): [string, string] => [color, tone(color)]))).toEqual([]);
  });

  it('gives provider names the same contrast', () => {
    const types = ['openrouter', 'opencode', 'opencode-go', 'anthropic', 'openai', 'groq', 'zai', 'something-new'];
    for (const type of types) {
      expect(providerTextColor(type)).toBe(tone(providerColor(type)));
    }
  });

  it('keeps the price colours readable too', () => {
    const entries = [0, 0.05, 0.3, 0.7, 2, 10].map((price): [string, string] => [
      `$${price}`,
      blendedColor(price) ?? '',
    ]);
    entries.push(['free', blendedColor(null, true) ?? '']);
    expect(failures(entries)).toEqual([]);
  });
});

describe('light badges', () => {
  const resolved = cssVariablesResolver(mergeMantineTheme(DEFAULT_THEME, theme));

  // Mantine's own defaults: shade 9 on a shade 1 background in light mode, shade 0 on a darkened shade 9 in dark mode.
  function badgeText(color: string, scheme: Scheme): string {
    const override = resolved[scheme][`--mantine-color-${color}-light-color`];
    return override ?? `var(--mantine-color-${color}-${scheme === 'light' ? 9 : 0})`;
  }

  it('reaches WCAG AA on the badge background and its hover background, in both schemes', () => {
    const found: string[] = [];
    for (const scheme of ['light', 'dark'] as const) {
      for (const color of COLORS) {
        const text = resolve(badgeText(color, scheme), scheme);
        const backgrounds: Record<string, Rgb> =
          scheme === 'light'
            ? { badge: fromHex(PALETTE[color][1]), hover: fromHex(PALETTE[color][2]) }
            : {
                badge: fromRgba(darken(PALETTE[color][9], 0.5)),
                hover: fromRgba(darken(PALETTE[color][9], 0.3)),
              };
        for (const [where, background] of Object.entries(backgrounds)) {
          const ratio = contrast(text, background);
          if (ratio < AA) {
            found.push(`${color} in ${scheme} mode on the ${where} background: ${ratio.toFixed(2)}`);
          }
        }
      }
    }
    expect(found).toEqual([]);
  });
});
