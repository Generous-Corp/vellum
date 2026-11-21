import { createHash } from 'node:crypto';
import { access, mkdir, readFile, realpath, stat, writeFile } from 'node:fs/promises';
import { basename, dirname, join, resolve, sep } from 'node:path';
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
const importedDesignPath = process.argv[4] ? resolve(process.argv[4]) : null;
let importedDesign = null;
let importedBindings = null;
if (importedDesignPath) {
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

async function packageImportedPngs() {
    const generated = dirname(importedDesignPath);
    const ui = dirname(generated);
    const key = importedDesign.source?.key;
    if (basename(generated) !== 'generated' || basename(ui) !== 'ui' ||
        typeof key !== 'string' || !/^[a-z][a-z0-9-]{0,63}$/.test(key)) return;

    const project = await realpath(dirname(ui));
    const files = join(project, 'assets', 'generated', key, 'files');
    for (const asset of importedDesign.assets ?? []) {
        if (asset.mimeType !== 'image/png') continue;
        const hash = /^sha256:([0-9a-f]{64})$/.exec(asset.contentHash)?.[1];
        if (!hash || asset.uri !== `assets/${hash}.png`) continue;

        const source = await realpath(join(files, asset.uri)).catch((error) => {
            if (error.code === 'ENOENT') return null;
            throw error;
        });
        if (!source) continue;
        if (!source.startsWith(files + sep)) {
            throw new Error(`Imported PNG escapes its generated asset directory: ${asset.uri}`);
        }
        const info = await stat(source);
        if (!info.isFile() || info.size > 64 * 1024 * 1024) {
            throw new Error(`Imported PNG is not a file within the image byte limit: ${asset.uri}`);
        }
        const bytes = await readFile(source);
        if (createHash('sha256').update(bytes).digest('hex') !== hash) {
            throw new Error(`Imported PNG does not match its content hash: ${asset.uri}`);
        }
        const directory = `${output}.images`;
        await mkdir(directory, { recursive: true });
        await writeFile(join(directory, `${hash}.png`), bytes);
    }
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
if (format === 'iife' && importedDesign) await packageImportedPngs();
await finalizeBundleSourceMap({
    bundlePath: output,
    projectRoot: process.env.VELLUM_PROJECT_ROOT ?? await findProjectRoot(entry),
    packageRoot,
});
process.stdout.write(`${output}\n`);
