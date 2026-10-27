import { providerColor } from '../lib/providerColors';

/** A small colour chip identifying a provider, also used as the text colour for its name. */
export function ProviderDot({ type, size = 8 }: { type: string | null | undefined; size?: number }) {
  return (
    <span
      aria-hidden
      data-provider-color={providerColor(type)}
      style={{
        display: 'inline-block',
        width: size,
        height: size,
        borderRadius: '50%',
        flex: 'none',
        background: `var(--mantine-color-${providerColor(type)}-6)`,
      }}
    />
  );
}
