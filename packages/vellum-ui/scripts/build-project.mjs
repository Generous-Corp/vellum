import { mkdir, readFile } from 'node:fs/promises';
import { basename, dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { build } from 'esbuild';

if (process.argv.length < 4 || process.argv.length > 5) {
    process.stderr.write('usage: build-project.mjs ENTRY OUTPUT [MATERIALIZED_DESIGN_IR]\n');
    process.exit(2);
}

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const entry = resolve(process.argv[2]);
const output = resolve(process.argv[3]);
let importedDesign = null;
let importedBindings = null;
if (process.argv[4]) {
    const importedDesignPath = resolve(process.argv[4]);
    importedDesign = JSON.parse(await readFile(importedDesignPath, 'utf8'));
    const bindingName = basename(importedDesignPath).replace(
        /\.materialized\.json$/, '.bindings.json',
    );
    if (bindingName === basename(importedDesignPath)) {
        throw new Error('materialized DesignIR must use the .materialized.json suffix');
    }
    importedBindings = JSON.parse(
        await readFile(join(dirname(importedDesignPath), bindingName), 'utf8'),
    );
}
await mkdir(dirname(output), { recursive: true });
await build({
    entryPoints: [entry],
    outfile: output,
    bundle: true,
    format: 'iife',
    platform: 'neutral',
    target: ['safari15'],
    jsx: 'automatic',
    jsxImportSource: '@vellum/ui',
    alias: {
        '@vellum/ui': resolve(packageRoot, 'src/index.js'),
        '@vellum/ui/jsx-runtime': resolve(packageRoot, 'src/jsx-runtime.js'),
        '@vellum/ui/jsx-dev-runtime': resolve(packageRoot, 'src/jsx-runtime.js'),
    },
    plugins: [{
        name: 'vellum-imported-design',
        setup(buildContext) {
            buildContext.onResolve({ filter: /^@vellum\/imported$/ }, () => ({
                path: '@vellum/imported', namespace: 'vellum-imported',
            }));
            buildContext.onLoad({ filter: /.*/, namespace: 'vellum-imported' }, () => ({
                contents:
                    `export const importedDesign = ${JSON.stringify(importedDesign)};\n` +
                    `export const importedBindings = ${JSON.stringify(importedBindings)};`,
                loader: 'js',
            }));
        },
    }],
    logLevel: 'warning',
});
process.stdout.write(`${output}\n`);
