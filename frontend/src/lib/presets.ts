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

const OPENROUTER = 'https://openrouter.ai';

function encodeSegment(segment: string): string {
  // "." and "..": the browser would resolve them away, and the link would leave the model pages.
  if (segment === '.' || segment === '..') {
    return segment.replace(/\./g, '%2E');
  }
  // A colon is legal in a path and is how OpenRouter spells a variant (":free"), so it stays readable.
  return encodeURIComponent(segment).replace(/%3A/gi, ':');
}

/** A model's page on OpenRouter. The id's own slash stays, as OpenRouter's addresses are author/slug[:variant]. */
export function openRouterModelUrl(modelId: string): string {
  try {
    const segments = modelId
      .trim()
      .split('/')
      .filter((segment) => segment !== '')
      .map(encodeSegment);
    if (segments.length > 0) {
      return `${OPENROUTER}/${segments.join('/')}`;
    }
  } catch {
    // encodeURIComponent throws on a lone surrogate; a link that opens the model list beats a page that will not render.
  }
  return `${OPENROUTER}/models`;
}
