import { access, mkdir, readFile } from 'node:fs/promises';
import { basename, dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { build } from 'esbuild';
import { checkPortability } from './check-portability.mjs';
import { finalizeBundleSourceMap } from './source-map.mjs';

if (process.argv.length < 4 || process.argv.length > 5) {
    process.stderr.write('usage: build-project.mjs ENTRY OUTPUT [MATERIALIZED_DESIGN_IR]\n');
    process.exit(2);
}

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const entry = resolve(process.argv[2]);
const output = resolve(process.argv[3]);
const format = process.env.VELLUM_BUILD_FORMAT ?? 'iife';
if (!['iife', 'esm'].includes(format)) {
    throw new Error('VELLUM_BUILD_FORMAT must be iife or esm');
}
const portabilityTarget = format === 'esm' ? 'web' : 'native';
const portability = await checkPortability(entry, { target: portabilityTarget });
if (portability.status !== 'passed') {
    process.stderr.write(`${JSON.stringify(portability)}\n`);
    process.exit(1);
}
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

async function findProjectRoot(start) {
    let directory = dirname(start);
    while (true) {
        for (const marker of ['app.toml', 'vellum.lock']) {
            try {
                await access(join(directory, marker));
                return directory;
            } catch (error) {
                if (error.code !== 'ENOENT') throw error;
            }
        }
        const parent = dirname(directory);
        if (parent === directory) return dirname(start);
        directory = parent;
    }
}

await mkdir(dirname(output), { recursive: true });
await build({
    entryPoints: [entry],
    outfile: output,
    bundle: true,
    format,
    platform: 'neutral',
    target: ['safari15'],
    sourcemap: 'external',
    sourcesContent: true,
    minifyWhitespace: true,
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
await finalizeBundleSourceMap({
    bundlePath: output,
    projectRoot: process.env.VELLUM_PROJECT_ROOT ?? await findProjectRoot(entry),
    packageRoot,
});
process.stdout.write(`${output}\n`);
