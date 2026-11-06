import { describe, expect, it } from 'vitest';
import { openRouterModelUrl, presetName, presetSlug } from './presets';

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
