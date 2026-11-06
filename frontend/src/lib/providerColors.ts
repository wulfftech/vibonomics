import { tone } from './tone';

/** Deterministic colours for providers: known brands first, a stable hash for anything else. */

const KNOWN: Record<string, string> = {
  openrouter: 'indigo',
  opencode: 'teal',
  'opencode-go': 'cyan',
  anthropic: 'orange',
  openai: 'green',
  google: 'blue',
  deepseek: 'grape',
  xai: 'gray',
  mistral: 'red',
  groq: 'yellow',
  moonshot: 'pink',
  zai: 'lime',
  together: 'violet',
  fireworks: 'grape',
  cerebras: 'cyan',
  requesty: 'indigo',
  chutes: 'teal',
  nvidia: 'green',
  'custom-openai': 'gray',
};

const FALLBACK = [
  'blue',
  'teal',
  'violet',
  'orange',
  'grape',
  'cyan',
  'lime',
  'pink',
  'indigo',
  'green',
  'yellow',
  'red',
];

export function providerColor(type: string | null | undefined): string {
  if (!type) {
    return 'gray';
  }
  const known = KNOWN[type];
  if (known) {
    return known;
  }
  let hash = 0;
  for (let index = 0; index < type.length; index += 1) {
    hash = (hash * 31 + type.charCodeAt(index)) >>> 0;
  }
  return FALLBACK[hash % FALLBACK.length];
}

/** Readable provider text colour in both schemes; the dot uses the filled shade. */
export function providerTextColor(type: string | null | undefined): string {
  return tone(providerColor(type));
}
