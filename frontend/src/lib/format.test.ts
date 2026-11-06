import { describe, expect, it } from 'vitest';
import { compactTokens, money } from './format';

describe('money', () => {
  it('formats dollars with the precision the amount needs', () => {
    expect(money(0)).toBe('$0');
    expect(money(1234.5)).toBe('$1,235');
    expect(money(350)).toBe('$350');
    expect(money(12.5)).toBe('$12.50');
    expect(money(0.075)).toBe('$0.075');
    expect(money(0.4)).toBe('$0.40');
    expect(money(0.0004)).toBe('$0.0004');
    expect(money(-5)).toBe('−$5.00');
    expect(money(null)).toBe('—');
  });

  it('reads a missing currency, and USD in any case, as dollars', () => {
    expect(money(12.5, null)).toBe('$12.50');
    expect(money(12.5, undefined)).toBe('$12.50');
    expect(money(12.5, 'USD')).toBe('$12.50');
    expect(money(12.5, 'usd')).toBe('$12.50');
  });

  it('writes yuan with its own symbol, so 350 CNY is not read as $350', () => {
    expect(money(350, 'CNY')).toBe('¥350');
    expect(money(12.5, 'cny')).toBe('¥12.50');
    expect(money(0, 'CNY')).toBe('¥0');
  });

  it('writes any other currency as its code and a no-break space', () => {
    expect(money(12.5, 'CHF')).toBe('CHF\u00a012.50');
    expect(money(-3, 'AUD')).toBe('−AUD\u00a03.00');
    expect(money(0, 'EUR')).toBe('EUR\u00a00');
  });

  it('never uses exponent notation for a tiny amount', () => {
    expect(money(5e-7)).toBe('<$0.000001');
    expect(money(5e-7, 'CNY')).toBe('<¥0.000001');
    expect(money(-5e-7)).toBe('>−$0.000001');
    expect(money(1.2e-6)).toBe('$0.0000012');
    for (const value of [5e-7, 1e-9, 1e-15, 0.0000123, 1e-6]) {
      expect(money(value)).not.toMatch(/e[-+]?\d/i);
    }
  });
});

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
