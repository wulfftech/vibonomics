import { describe, expect, it } from 'vitest';
import { compactTokens } from './format';

describe('compactTokens', () => {
  it('reads powers of two in binary units', () => {
    expect(compactTokens(131072)).toBe('128k');
    expect(compactTokens(65536)).toBe('64k');
    expect(compactTokens(1048576)).toBe('1M');
  });

  it('reads round decimal sizes in decimal units, even when they divide by 1024', () => {
    expect(compactTokens(128000)).toBe('128k');
    expect(compactTokens(256000)).toBe('256k');
    expect(compactTokens(512000)).toBe('512k');
    expect(compactTokens(200000)).toBe('200k');
    expect(compactTokens(1000000)).toBe('1M');
  });

  it('shows a dash for missing sizes', () => {
    expect(compactTokens(null)).toBe('—');
    expect(compactTokens(0)).toBe('—');
  });
});
