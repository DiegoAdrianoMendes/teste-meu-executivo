import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

describe('responsive contrast CSS scope', () => {
  it('does not leak app contrast overrides to the public surface', () => {
    const css = readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf8');
    const media = css.match(/@media \(max-width: 1279px\) \{([\s\S]*?)\n\}/)?.[1] ?? '';
    expect(media).toContain('.meu-executivo-shell .text-muted-foreground');
    expect(media).toContain('.meu-executivo-shell [class*="text-muted-foreground/"]');
    expect(media).not.toMatch(/^\s*\.text-muted-foreground\s*\{/m);
    expect(media).not.toMatch(/^\s*\[class\*="text-muted-foreground\/"\]\s*\{/m);
  });
});
