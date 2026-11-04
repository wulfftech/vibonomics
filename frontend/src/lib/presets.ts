/** The prefix every preset Vibonomics writes starts with; it is how the backend knows a preset is its own. */
export const PRESET_PREFIX = 'vibonomics-';

/** The slug a typed name becomes, as the backend makes it: lower case, hyphenated, prefixed. Empty for no name. */
export function presetSlug(name: string): string {
  const base = name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  if (base === '') {
    return '';
  }
  return base.startsWith(PRESET_PREFIX) ? base : PRESET_PREFIX + base;
}

/** The part of a slug a person types: the slug without its prefix. */
export function presetName(slug: string): string {
  return slug.toLowerCase().startsWith(PRESET_PREFIX) ? slug.slice(PRESET_PREFIX.length) : slug;
}
