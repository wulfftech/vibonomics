import { describe, expect, it } from 'vitest';
import { PRESET_DEFAULTS, type CatalogPreset } from '../api/types';
import {
  itemSlug,
  matchesSearch,
  openRouterModelUrl,
  openRouterPresetUrl,
  presetEntries,
  presetName,
  presetSlug,
} from './presets';

describe('presetSlug', () => {
  it('makes a typed name into a prefixed slug, as the backend does', () => {
    expect(presetSlug('best value')).toBe('vibonomics-best-value');
    expect(presetSlug('  My Coding Preset! ')).toBe('vibonomics-my-coding-preset');
    expect(presetSlug('Vibonomics Fast')).toBe('vibonomics-fast');
    expect(presetSlug('vibonomics-fast')).toBe('vibonomics-fast');
  });

  it('has nothing for a name with nothing in it', () => {
    expect(presetSlug('')).toBe('');
    expect(presetSlug('  !!! ')).toBe('');
  });
});

describe('presetName', () => {
  it('is the slug without its prefix', () => {
    expect(presetName('vibonomics-best-value')).toBe('best-value');
    expect(presetName('Vibonomics-Best')).toBe('Best');
    expect(presetName('best value')).toBe('best value');
  });
});

describe('openRouterModelUrl', () => {
  it('puts an ordinary id after the host, keeping the slash between author and slug', () => {
    expect(openRouterModelUrl('deepseek/deepseek-v4-flash')).toBe('https://openrouter.ai/deepseek/deepseek-v4-flash');
  });

  it('keeps a variant suffix such as :free as part of the last segment, colon and all', () => {
    expect(openRouterModelUrl('dots-studio/dots-3-note-preview:free')).toBe(
      'https://openrouter.ai/dots-studio/dots-3-note-preview:free',
    );
    expect(openRouterModelUrl('acme/model:nitro')).toBe('https://openrouter.ai/acme/model:nitro');
  });

  it('leaves a tilde alone and encodes anything that would change the meaning of the URL', () => {
    expect(openRouterModelUrl('~anthropic/claude-opus-latest')).toBe(
      'https://openrouter.ai/~anthropic/claude-opus-latest',
    );
    expect(openRouterModelUrl('acme/model v2#beta?x=1%')).toBe(
      'https://openrouter.ai/acme/model%20v2%23beta%3Fx%3D1%25',
    );
    expect(openRouterModelUrl('acme/modèle')).toBe('https://openrouter.ai/acme/mod%C3%A8le');
  });

  it('cannot be turned into a path off the model pages', () => {
    expect(openRouterModelUrl('../admin')).toBe('https://openrouter.ai/%2E%2E/admin');
    expect(openRouterModelUrl('acme/./x')).toBe('https://openrouter.ai/acme/%2E/x');
  });

  it('drops empty segments, so stray slashes do not make a different address', () => {
    expect(openRouterModelUrl('/acme//model/')).toBe('https://openrouter.ai/acme/model');
  });

  it('goes to the list of models for an id with nothing in it, or that cannot be encoded', () => {
    expect(openRouterModelUrl('')).toBe('https://openrouter.ai/models');
    expect(openRouterModelUrl('   ')).toBe('https://openrouter.ai/models');
    expect(openRouterModelUrl('/')).toBe('https://openrouter.ai/models');
    expect(openRouterModelUrl('acme/\uD800')).toBe('https://openrouter.ai/models');
  });
});

describe('openRouterPresetUrl', () => {
  it('opens the preset in its workspace', () => {
    expect(openRouterPresetUrl('pre_123', 'my-team')).toBe(
      'https://openrouter.ai/workspaces/my-team/presets/pre_123/edit',
    );
  });

  it('assumes the default workspace when OpenRouter named none', () => {
    const url = 'https://openrouter.ai/workspaces/default/presets/pre_123/edit';
    expect(openRouterPresetUrl('pre_123')).toBe(url);
    expect(openRouterPresetUrl('pre_123', null)).toBe(url);
    expect(openRouterPresetUrl('pre_123', '  ')).toBe(url);
  });

  it('has no page to open without an id', () => {
    expect(openRouterPresetUrl(null)).toBeNull();
    expect(openRouterPresetUrl(undefined, 'my-team')).toBeNull();
    expect(openRouterPresetUrl('   ', 'my-team')).toBeNull();
  });

  it('encodes each segment, so neither can change the path', () => {
    expect(openRouterPresetUrl('a/b?c#d', 'x y/z')).toBe(
      'https://openrouter.ai/workspaces/x%20y%2Fz/presets/a%2Fb%3Fc%23d/edit',
    );
    expect(openRouterPresetUrl('..', '.')).toBe('https://openrouter.ai/workspaces/%2E/presets/%2E%2E/edit');
  });

  it('gives no link for an id that cannot be encoded', () => {
    expect(openRouterPresetUrl('\uD800', 'default')).toBeNull();
    expect(openRouterPresetUrl('pre_1', '\uD800')).toBeNull();
  });
});

describe('itemSlug', () => {
  it('adds the prefix to a typed name, and keeps an adopted name as it is', () => {
    expect(itemSlug({ slug: 'Best Value', adopted: false })).toBe('vibonomics-best-value');
    expect(itemSlug({ slug: 'vibonomics-fast', adopted: false })).toBe('vibonomics-fast');
    expect(itemSlug({ slug: 'my-hand-made', adopted: true })).toBe('my-hand-made');
  });
});

function live(slug: string, over: Partial<CatalogPreset> = {}): CatalogPreset {
  return {
    slug,
    preset_id: `id-${slug}`,
    name: slug,
    status: 'active',
    version: 1,
    updated_at: null,
    models: [],
    providers: [],
    config: {},
    has_system_prompt: false,
    passthrough_keys: [],
    managed: false,
    ...over,
  };
}

describe('presetEntries', () => {
  it('lists the settings first, then presets it made, then the rest of the account by name', () => {
    const catalog = [
      live('zebra', { name: 'Zebra' }),
      live('vibonomics-old', { name: 'Old', managed: true }),
      live('apple', { name: 'Apple' }),
      live('vibonomics-kept', { name: 'Kept', managed: true, version: 4, status: 'archived' }),
    ];
    const items = [
      { ...PRESET_DEFAULTS, slug: 'vibonomics-kept' },
      { ...PRESET_DEFAULTS, slug: 'brand-new' },
    ];
    const rows = presetEntries(catalog, items);
    expect(rows.map((r) => r.slug)).toEqual([
      'vibonomics-kept',
      'vibonomics-brand-new',
      'vibonomics-old',
      'apple',
      'zebra',
    ]);

    const [kept, fresh, old, apple] = rows;
    expect(kept).toMatchObject({ name: 'Kept', version: 4, archived: true, managed: true, notCreated: false });
    expect(kept.item).toBe(items[0]);
    expect(fresh).toMatchObject({
      name: 'vibonomics-brand-new',
      version: null,
      managed: true,
      notCreated: true,
      catalog: null,
    });
    expect(old).toMatchObject({ managed: true, notCreated: false, item: null });
    expect(apple).toMatchObject({ managed: false, notCreated: false, item: null, archived: false });
  });

  it('matches an adopted item to the preset of the same name, without a prefix', () => {
    const rows = presetEntries(
      [live('hand-made', { managed: true })],
      [{ ...PRESET_DEFAULTS, slug: 'hand-made', adopted: true }],
    );
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ slug: 'hand-made', managed: true, notCreated: false });
  });

  it('shows a name once, however many items claim it', () => {
    const rows = presetEntries(
      [],
      [
        { ...PRESET_DEFAULTS, slug: 'twice' },
        { ...PRESET_DEFAULTS, slug: 'Twice' },
      ],
    );
    expect(rows.map((r) => r.slug)).toEqual(['vibonomics-twice']);
  });
});

describe('matchesSearch', () => {
  it('looks in the name and the slug without minding case, and matches everything for nothing typed', () => {
    const entry = { name: 'Fast coding', slug: 'vibonomics-fast' };
    expect(matchesSearch(entry, '')).toBe(true);
    expect(matchesSearch(entry, '  ')).toBe(true);
    expect(matchesSearch(entry, 'CODING')).toBe(true);
    expect(matchesSearch(entry, 'nomics-fa')).toBe(true);
    expect(matchesSearch(entry, 'slow')).toBe(false);
  });
});
