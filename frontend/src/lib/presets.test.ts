import { describe, expect, it } from 'vitest';
import { presetName, presetSlug } from './presets';

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
