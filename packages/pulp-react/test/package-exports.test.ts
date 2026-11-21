import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { expect, it } from 'vitest';

it('loads the advertised require export without synchronous ESM support', () => {
    const metadata = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'));
    const requirePath = createRequire(import.meta.url).resolve(`../${metadata.exports['.'].require}`);
    const output = execFileSync(process.execPath, ['--no-experimental-require-module', '-e',
        `const api = require(${JSON.stringify(requirePath)}); process.stdout.write(typeof api.render);`], { encoding: 'utf8' });
    expect(output).toBe('function');
});
