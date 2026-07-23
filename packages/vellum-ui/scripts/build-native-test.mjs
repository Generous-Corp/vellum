import { mkdir } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { build } from 'esbuild';

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const output = resolve(process.argv[2] ?? `${packageRoot}/build/native-app.iife.js`);
await mkdir(dirname(output), { recursive: true });
await build({
    entryPoints: [`${packageRoot}/test/fixtures/native-app.jsx`],
    outfile: output,
    bundle: true,
    format: 'iife',
    platform: 'neutral',
    target: ['safari15'],
    jsx: 'automatic',
    jsxImportSource: '@vellum/ui',
    logLevel: 'warning',
});
process.stdout.write(`${output}\n`);
