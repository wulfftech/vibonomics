import { describe, expect, it } from 'vitest';
import { GUARDRAIL_KEYS, NO_GUARDRAILS, normalizeGuardrails, type PresetPlan } from '../api/types';
import {
  asGuardrails,
  describeLive,
  guardrailMode,
  guardrailProblems,
  hasGuardrailProblems,
  liveGuardrail,
  livePreset,
  seedFromLive,
  setGuardrailMode,
  setGuardrailValue,
} from './presets';

describe('guardrailMode and setGuardrailMode', () => {
  it('reads Leave from a key that is null and not in clear, Set from a value, Remove from clear', () => {
    const g = { ...NO_GUARDRAILS, temperature: 0.3, top_p: null, clear: ['max_tokens' as const] };
    expect(guardrailMode(g, 'temperature')).toBe('set');
    expect(guardrailMode(g, 'top_p')).toBe('leave');
    expect(guardrailMode(g, 'max_tokens')).toBe('remove');
  });

  it('takes a key round the three states and back to where it began', () => {
    const start = NO_GUARDRAILS;
    const set = setGuardrailValue(setGuardrailMode(start, 'temperature', 'set'), 'temperature', 0.5);
    expect(set).toEqual({ ...NO_GUARDRAILS, temperature: 0.5 });
    expect(guardrailMode(set, 'temperature')).toBe('set');

    const removed = setGuardrailMode(set, 'temperature', 'remove');
    expect(removed).toEqual({ ...NO_GUARDRAILS, clear: ['temperature'] });
    expect(guardrailMode(removed, 'temperature')).toBe('remove');

    const left = setGuardrailMode(removed, 'temperature', 'leave');
    expect(left).toEqual(NO_GUARDRAILS);
    expect(guardrailMode(left, 'temperature')).toBe('leave');
    // the same text, so a draft that went round compares equal to what was saved
    expect(JSON.stringify(left)).toBe(JSON.stringify(start));
  });

  it('never leaves a key both set and removed, whichever way it gets there', () => {
    const removed = setGuardrailMode(NO_GUARDRAILS, 'max_tokens', 'remove');
    const set = setGuardrailValue(removed, 'max_tokens', 4000);
    expect(set.max_tokens).toBe(4000);
    expect(set.clear).toEqual([]);

    const back = setGuardrailMode(set, 'max_tokens', 'remove');
    expect(back.max_tokens).toBeNull();
    expect(back.clear).toEqual(['max_tokens']);

    const seeded = setGuardrailMode(back, 'max_tokens', 'set', 8000);
    expect(seeded.max_tokens).toBe(8000);
    expect(seeded.clear).toEqual([]);
    for (const key of GUARDRAIL_KEYS) {
      for (const mode of ['leave', 'set', 'remove'] as const) {
        const g = setGuardrailMode(NO_GUARDRAILS, key, mode);
        expect(g[key] !== null && g.clear.includes(key)).toBe(false);
      }
    }
  });

  it('keeps clear in the server order, so a draft does not differ from the saved one by order alone', () => {
    let g = setGuardrailMode(NO_GUARDRAILS, 'top_p', 'remove');
    g = setGuardrailMode(g, 'temperature', 'remove');
    g = setGuardrailMode(g, 'system_prompt', 'remove');
    expect(g.clear).toEqual(['temperature', 'top_p', 'system_prompt']);
    expect(normalizeGuardrails({ clear: ['system_prompt', 'top_p', 'temperature', 'top_p'] }).clear).toEqual(g.clear);
  });

  it('keeps the value a key already has when it is set again, and starts an empty one otherwise', () => {
    const has = setGuardrailValue(NO_GUARDRAILS, 'temperature', 0.7);
    expect(setGuardrailMode(has, 'temperature', 'set').temperature).toBe(0.7);
    expect(setGuardrailMode(NO_GUARDRAILS, 'temperature', 'set').temperature).toBe('');
    expect(setGuardrailMode(NO_GUARDRAILS, 'quantizations', 'set').quantizations).toEqual([]);
    expect(setGuardrailMode(NO_GUARDRAILS, 'ignore', 'set').ignore).toEqual([]);
    expect(setGuardrailMode(NO_GUARDRAILS, 'require_parameters', 'set').require_parameters).toBe(true);
    expect(setGuardrailMode(NO_GUARDRAILS, 'system_prompt', 'set').system_prompt).toBe('');
    expect(setGuardrailMode(NO_GUARDRAILS, 'temperature', 'set', 0.2).temperature).toBe(0.2);
  });

  it('leaves the other keys alone, and does not change what it was given', () => {
    const before = { ...NO_GUARDRAILS, top_p: 0.9, clear: ['ignore' as const] };
    const copy = structuredClone(before);
    const after = setGuardrailMode(before, 'temperature', 'remove');
    expect(after.top_p).toBe(0.9);
    expect(after.clear).toEqual(['temperature', 'ignore']);
    expect(before).toEqual(copy);
  });

  it('treats a saved item that lacks guardrails, or some of them, as every key left alone', () => {
    expect(guardrailMode(undefined, 'temperature')).toBe('leave');
    expect(guardrailMode({ temperature: 1 }, 'temperature')).toBe('set');
    expect(guardrailMode({ temperature: 1 }, 'top_p')).toBe('leave');
    expect(setGuardrailValue(undefined, 'top_p', 0.5)).toEqual({ ...NO_GUARDRAILS, top_p: 0.5 });
  });
});

describe('guardrailProblems', () => {
  it('finds none in guardrails that are left alone, removed, or hold good values', () => {
    expect(guardrailProblems(NO_GUARDRAILS)).toEqual({});
    expect(guardrailProblems(setGuardrailMode(NO_GUARDRAILS, 'ignore', 'remove'))).toEqual({});
    const good = {
      ...NO_GUARDRAILS,
      temperature: 0,
      max_tokens: 2_000_000,
      repetition_penalty: 1.1,
      frequency_penalty: -2,
      presence_penalty: 2,
      top_p: 1,
      quantizations: ['fp8' as const],
      ignore: ['deepinfra', 'google-vertex/europe'],
      require_parameters: false,
      system_prompt: 'Be brief.',
    };
    expect(guardrailProblems(good)).toEqual({});
    expect(hasGuardrailProblems(good)).toBe(false);
  });

  it('flags a Set number that is empty or out of range, per key', () => {
    const bad = setGuardrailMode(NO_GUARDRAILS, 'temperature', 'set');
    expect(guardrailProblems(bad).temperature).toBe('Enter a number from 0 to 2');
    expect(hasGuardrailProblems(bad)).toBe(true);
    expect(guardrailProblems(setGuardrailValue(NO_GUARDRAILS, 'temperature', 2.5)).temperature).toBeDefined();
    expect(guardrailProblems(setGuardrailValue(NO_GUARDRAILS, 'temperature', Number.NaN)).temperature).toBeDefined();
    expect(guardrailProblems(setGuardrailValue(NO_GUARDRAILS, 'repetition_penalty', 0)).repetition_penalty).toBe(
      'Enter a number from 0.01 to 2',
    );
    expect(guardrailProblems(setGuardrailValue(NO_GUARDRAILS, 'frequency_penalty', -2.1)).frequency_penalty).toBe(
      'Enter a number from -2 to 2',
    );
    expect(guardrailProblems(setGuardrailValue(NO_GUARDRAILS, 'top_p', 1.01)).top_p).toBe(
      'Enter a number from 0.01 to 1',
    );
  });

  it('wants max_tokens whole and within 1 to 2,000,000', () => {
    const message = 'Enter a whole number from 1 to 2,000,000';
    expect(guardrailProblems(setGuardrailValue(NO_GUARDRAILS, 'max_tokens', 0)).max_tokens).toBe(message);
    expect(guardrailProblems(setGuardrailValue(NO_GUARDRAILS, 'max_tokens', 1.5)).max_tokens).toBe(message);
    expect(guardrailProblems(setGuardrailValue(NO_GUARDRAILS, 'max_tokens', 2_000_001)).max_tokens).toBe(message);
    expect(guardrailProblems(setGuardrailValue(NO_GUARDRAILS, 'max_tokens', 1)).max_tokens).toBeUndefined();
  });

  it('flags an empty list, an invalid provider slug and too many providers', () => {
    expect(guardrailProblems(setGuardrailMode(NO_GUARDRAILS, 'quantizations', 'set')).quantizations).toMatch(
      /at least one precision/,
    );
    expect(guardrailProblems(setGuardrailMode(NO_GUARDRAILS, 'ignore', 'set')).ignore).toMatch(/at least one provider/);
    expect(guardrailProblems(setGuardrailValue(NO_GUARDRAILS, 'ignore', ['deepinfra', 'bad slug'])).ignore).toMatch(
      /"bad slug" is not a provider slug/,
    );
    expect(guardrailProblems(setGuardrailValue(NO_GUARDRAILS, 'ignore', ['a/b/c'])).ignore).toBeDefined();
    const many = Array.from({ length: 51 }, (_, i) => `p${i}`);
    expect(guardrailProblems(setGuardrailValue(NO_GUARDRAILS, 'ignore', many)).ignore).toBe('At most 50 providers');
  });

  it('flags a blank system prompt and one over 20,000 characters', () => {
    expect(guardrailProblems(setGuardrailMode(NO_GUARDRAILS, 'system_prompt', 'set')).system_prompt).toMatch(
      /Write the prompt/,
    );
    expect(guardrailProblems(setGuardrailValue(NO_GUARDRAILS, 'system_prompt', '  \n ')).system_prompt).toBeDefined();
    expect(
      guardrailProblems(setGuardrailValue(NO_GUARDRAILS, 'system_prompt', 'x'.repeat(20_000))).system_prompt,
    ).toBeUndefined();
    expect(guardrailProblems(setGuardrailValue(NO_GUARDRAILS, 'system_prompt', 'x'.repeat(20_001))).system_prompt).toBe(
      'At most 20,000 characters',
    );
  });

  it('names a key that is somehow both set and removed', () => {
    const both = { ...NO_GUARDRAILS, temperature: 1, clear: ['temperature' as const] };
    expect(guardrailProblems(both).temperature).toMatch(/both set and removed/);
  });

  it('only lets a draft with no problems be turned back into the saved type', () => {
    const draft = setGuardrailValue(NO_GUARDRAILS, 'temperature', 0.4);
    expect(hasGuardrailProblems(draft)).toBe(false);
    expect(asGuardrails(draft).temperature).toBe(0.4);
  });
});

describe('the live value of a guardrail', () => {
  const config = {
    model: 'a/b',
    temperature: 0.2,
    max_tokens: 4096,
    provider: { only: ['x'], quantizations: ['fp8'], ignore: ['deepinfra'], require_parameters: true },
  };

  it('reads the top-level keys, and the three that live inside provider', () => {
    expect(liveGuardrail(config, 'temperature')).toBe(0.2);
    expect(liveGuardrail(config, 'max_tokens')).toBe(4096);
    expect(liveGuardrail(config, 'quantizations')).toEqual(['fp8']);
    expect(liveGuardrail(config, 'ignore')).toEqual(['deepinfra']);
    expect(liveGuardrail(config, 'require_parameters')).toBe(true);
    expect(liveGuardrail(config, 'top_p')).toBeUndefined();
  });

  it('has nothing for a preset that is not on OpenRouter, or has no provider block', () => {
    expect(liveGuardrail(null, 'temperature')).toBeUndefined();
    expect(liveGuardrail({ model: 'a/b' }, 'ignore')).toBeUndefined();
    expect(liveGuardrail({ provider: null, temperature: null }, 'ignore')).toBeUndefined();
    expect(liveGuardrail({ temperature: null }, 'temperature')).toBeUndefined();
  });

  it('says a value in a few words, or that it is not set', () => {
    expect(describeLive(undefined)).toBe('not set');
    expect(describeLive(0.2)).toBe('0.2');
    expect(describeLive(false)).toBe('false');
    expect(describeLive(['fp8', 'bf16'])).toBe('fp8, bf16');
    expect(describeLive([])).toBe('an empty list');
  });

  it('turns a live value into a starting point for Set only when it fits the key', () => {
    expect(seedFromLive('temperature', 0.2)).toBe(0.2);
    expect(seedFromLive('temperature', '0.2')).toBeUndefined();
    expect(seedFromLive('require_parameters', false)).toBe(false);
    expect(seedFromLive('quantizations', ['fp8', 'weird'])).toEqual(['fp8']);
    expect(seedFromLive('quantizations', ['weird'])).toBeUndefined();
    expect(seedFromLive('ignore', ['deepinfra', 3])).toEqual(['deepinfra']);
    expect(seedFromLive('ignore', [])).toBeUndefined();
  });
});

describe('livePreset', () => {
  const catalog = {
    slug: 's',
    preset_id: null,
    name: 's',
    status: 'active',
    version: 3,
    updated_at: null,
    models: [],
    providers: [],
    config: { temperature: 0.1 },
    has_system_prompt: true,
    passthrough_keys: [],
    managed: true,
  };
  const plan = (version: number | null, config: Record<string, unknown> | null) =>
    ({
      current_version: version,
      current_config: config,
      system_prompt: { live: 'Be kind.' },
    }) as unknown as PresetPlan;

  it('takes the plan when it is at least as new as the sync, and the sync when it is newer', () => {
    expect(livePreset(catalog, plan(3, { temperature: 0.5 }))).toEqual({
      config: { temperature: 0.5 },
      systemPrompt: 'Be kind.',
    });
    expect(livePreset(catalog, plan(2, { temperature: 0.5 }))).toEqual({
      config: { temperature: 0.1 },
      systemPrompt: undefined,
    });
  });

  it('falls back to what it has, and knows when the preset is not on OpenRouter', () => {
    expect(livePreset(catalog, undefined).config).toEqual({ temperature: 0.1 });
    expect(livePreset({ ...catalog, has_system_prompt: false }, undefined).systemPrompt).toBeNull();
    expect(livePreset(null, plan(4, { top_p: 1 })).config).toEqual({ top_p: 1 });
    expect(livePreset(null, plan(null, null))).toEqual({ config: null, systemPrompt: 'Be kind.' });
    expect(livePreset(null, undefined)).toEqual({ config: null, systemPrompt: null });
  });
});
