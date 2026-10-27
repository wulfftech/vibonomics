/** Display formatting shared by every page. Empty values render as an em dash. */

export const DASH = '—';

/** USD with sensible precision across $0.0004 per task and $75 per 1M tokens. */
export function money(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return DASH;
  }
  if (value === 0) {
    return '$0';
  }
  const abs = Math.abs(value);
  const sign = value < 0 ? '−' : '';
  if (abs >= 1000) {
    return `${sign}$${Math.round(abs).toLocaleString('en-US')}`;
  }
  if (abs >= 100) {
    return `${sign}$${abs.toFixed(0)}`;
  }
  if (abs >= 1) {
    return `${sign}$${abs.toFixed(2)}`;
  }
  if (abs >= 0.01) {
    // $0.075, $0.40
    const fixed = abs.toFixed(3);
    return `${sign}$${fixed.endsWith('0') ? abs.toFixed(2) : fixed}`;
  }
  return `${sign}$${Number(abs.toPrecision(2)).toString()}`;
}

/**
 * 131072 → "128k", 1048576 → "1M", 200000 → "200k", 128000 → "128k". Sizes that are a multiple of 1024
 * but not of 1000 are read in binary units; 128000 is 125 × 1024 and still means 128k.
 */
export function compactTokens(value: number | null | undefined): string {
  if (value === null || value === undefined || value <= 0) {
    return DASH;
  }
  const binary = value % 1024 === 0 && value % 1000 !== 0;
  const k = binary ? 1024 : 1000;
  const m = k * k;
  if (value >= m) {
    const n = value / m;
    return `${Number(n.toFixed(n < 10 ? 1 : 0)).toString()}M`;
  }
  if (value >= k) {
    return `${Math.round(value / k)}k`;
  }
  return String(value);
}

/** 1234567 → "1.2M" tokens; for counts rather than context sizes. */
export function compactCount(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return DASH;
  }
  if (value >= 1e6) {
    return `${Number((value / 1e6).toFixed(1)).toString()}M`;
  }
  if (value >= 1e3) {
    return `${Number((value / 1e3).toFixed(1)).toString()}k`;
  }
  return String(Math.round(value));
}

export function integer(value: number | null | undefined): string {
  return value === null || value === undefined ? DASH : Math.round(value).toLocaleString('en-US');
}

export function percent(fraction: number | null | undefined, digits = 0): string {
  return fraction === null || fraction === undefined ? DASH : `${(fraction * 100).toFixed(digits)}%`;
}

/** Parses an API timestamp; a value without a zone is UTC (SQLite drops the offset). */
export function parseDate(value: string | null | undefined): Date | null {
  if (!value) {
    return null;
  }
  const hasZone = /([zZ]|[+-]\d\d:?\d\d)$/.test(value);
  const isDateOnly = /^\d{4}-\d{2}-\d{2}$/.test(value);
  const date = new Date(hasZone || isDateOnly ? value : `${value}Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function relativeTime(value: string | null | undefined, now: number = Date.now()): string {
  const date = parseDate(value);
  if (date === null) {
    return DASH;
  }
  const seconds = Math.round((now - date.getTime()) / 1000);
  const future = seconds < 0;
  const abs = Math.abs(seconds);
  let text: string;
  if (abs < 45) {
    return 'just now';
  } else if (abs < 90 * 60) {
    text = `${Math.max(1, Math.round(abs / 60))} min`;
  } else if (abs < 36 * 3600) {
    text = `${Math.round(abs / 3600)} h`;
  } else if (abs < 45 * 86400) {
    const days = Math.round(abs / 86400);
    text = `${days} ${days === 1 ? 'day' : 'days'}`;
  } else if (abs < 365 * 86400) {
    const months = Math.round(abs / (30 * 86400));
    text = `${months} ${months === 1 ? 'month' : 'months'}`;
  } else {
    const years = Math.round(abs / (365 * 86400));
    text = `${years} ${years === 1 ? 'year' : 'years'}`;
  }
  return future ? `in ${text}` : `${text} ago`;
}

export function dateTime(value: string | null | undefined): string {
  const date = parseDate(value);
  return date === null ? DASH : date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

export function dateOnly(value: string | null | undefined): string {
  if (!value) {
    return DASH;
  }
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return value;
  }
  const date = parseDate(value);
  return date === null ? value : date.toISOString().slice(0, 10);
}

export function duration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) {
    return DASH;
  }
  if (ms < 1000) {
    return `${Math.round(ms)} ms`;
  }
  if (ms < 60_000) {
    return `${(ms / 1000).toFixed(1)} s`;
  }
  return `${Math.floor(ms / 60_000)} min ${Math.round((ms % 60_000) / 1000)} s`;
}

/** "−50%" for a 0.5 discount. */
export function discountLabel(discount: number): string {
  return `−${Math.round(discount * 100)}%`;
}
