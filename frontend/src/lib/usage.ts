import { DASH } from './format';

/** Colours for the usage charts. Marks carry the series colour; text uses the app's text tokens. */
export const CHART = {
  // Validated with the dataviz checks against both of this app's surfaces (white, and Mantine's dark body).
  series: 'light-dark(#2a78d6, #3987e5)',
  seriesTrack: 'light-dark(#cde2fb, #0d366b)',
  grid: 'light-dark(#e1e0d9, #2c2c2a)',
  axis: 'light-dark(#c3c2b7, #383835)',
  surface: 'var(--mantine-color-body)',
  text: 'var(--mantine-color-text)',
  muted: 'var(--mantine-color-dimmed)',
} as const;

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "2026-10-30" as "30 Oct", read by hand so no time zone can move it to the day before. */
export function dayLabel(iso: string): string {
  const [, month, day] = iso.split('-');
  const name = MONTHS[Number(month) - 1];
  return name === undefined || day === undefined ? iso : `${Number(day)} ${name}`;
}

/** "2026-10-30" as "Fri 30 Oct 2026". */
export function longDay(iso: string): string {
  const date = new Date(`${iso}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) {
    return iso;
  }
  const weekday = date.toLocaleDateString('en-GB', { weekday: 'short', timeZone: 'UTC' });
  return `${weekday} ${dayLabel(iso)} ${iso.slice(0, 4)}`;
}

/** 5704993149 → "5.70B", 54875160 → "54.9M", 12345 → "12.3k". Counts of tokens, which run to billions. */
export function bigCount(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return DASH;
  }
  const abs = Math.abs(value);
  const trim = (n: number, digits: number) => (n >= 100 ? n.toFixed(0) : n.toFixed(digits));
  if (abs >= 1e9) {
    return `${trim(value / 1e9, 2)}B`;
  }
  if (abs >= 1e6) {
    return `${trim(value / 1e6, 1)}M`;
  }
  if (abs >= 1e3) {
    return `${trim(value / 1e3, 1)}k`;
  }
  return String(Math.round(value));
}

/** A y-axis label: whole dollars from ten up, cents below, thousands compacted. */
export function axisMoney(value: number): string {
  if (value === 0) {
    return '$0';
  }
  if (value >= 10_000) {
    return `$${Number((value / 1000).toFixed(0))}k`;
  }
  if (value >= 1000) {
    return `$${Number((value / 1000).toFixed(1))}k`;
  }
  if (value >= 10) {
    return `$${Math.round(value)}`;
  }
  if (value >= 1) {
    return `$${Number(value.toFixed(2))}`;
  }
  return `$${Number(value.toPrecision(2))}`;
}

/**
 * Gridline ticks from 0 up to a round top that holds `max`: a clean step (1, 2, 2.5 or 5 times a power of ten) and
 * three to five intervals, whichever combination reaches `max` with the least to spare. Nothing to plot gives 0 to 1.
 */
export function axisTicks(max: number): { top: number; ticks: number[] } {
  if (!Number.isFinite(max) || max <= 0) {
    return { top: 1, ticks: [0, 0.25, 0.5, 0.75, 1] };
  }
  const exponent = Math.floor(Math.log10(max / 5));
  let best: { top: number; step: number; intervals: number } | null = null;
  for (let e = exponent; e <= exponent + 2; e++) {
    for (const base of [1, 2, 2.5, 5]) {
      const step = base * 10 ** e;
      for (const intervals of [4, 5, 3]) {
        const top = step * intervals;
        if (top >= max * 0.999999 && (best === null || top < best.top - 1e-12)) {
          best = { top, step, intervals };
        }
      }
    }
  }
  const { top, step, intervals } = best ?? { top: max, step: max / 4, intervals: 4 };
  const clean = (n: number) => Number(n.toPrecision(12));
  return { top: clean(top), ticks: Array.from({ length: intervals + 1 }, (_, i) => clean(step * i)) };
}

/** Which of `count` x labels to print so that none are closer than `minGap` pixels. */
export function labelIndexes(count: number, width: number, minGap = 64): number[] {
  if (count <= 0) {
    return [];
  }
  const fit = Math.max(1, Math.floor(width / minGap));
  const stride = Math.max(1, Math.ceil(count / fit));
  const picked: number[] = [];
  for (let i = count - 1; i >= 0; i -= stride) {
    picked.unshift(i); // counted back from the latest day, so that day is always labelled
  }
  return picked;
}

/** An SVG path through the points, lifting the pen at a gap (null) rather than drawing across it. */
export function linePath(points: ({ x: number; y: number } | null)[]): string {
  let path = '';
  let pen = false;
  for (const p of points) {
    if (p === null) {
      pen = false;
      continue;
    }
    path += `${pen ? 'L' : 'M'}${p.x.toFixed(1)} ${p.y.toFixed(1)}`;
    pen = true;
  }
  return path;
}

/** A column that is square at its baseline and has a rounded data end. */
export function columnPath(x: number, width: number, top: number, base: number, radius = 4): string {
  const height = base - top;
  if (height <= 0 || width <= 0) {
    return '';
  }
  const r = Math.min(radius, width / 2, height);
  const right = x + width;
  return `M${x} ${base}V${top + r}Q${x} ${top} ${x + r} ${top}H${right - r}Q${right} ${top} ${right} ${top + r}V${base}Z`;
}

/** The share of `part` in `whole` as a percentage label; a dash when there is no whole. */
export function shareLabel(part: number, whole: number): string {
  if (whole <= 0) {
    return DASH;
  }
  const share = (part / whole) * 100;
  return share > 0 && share < 1 ? '<1%' : `${Math.round(share)}%`;
}
