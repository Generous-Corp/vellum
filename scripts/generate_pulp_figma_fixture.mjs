#!/usr/bin/env node

import { createHash } from 'node:crypto';
import { execFileSync, spawnSync } from 'node:child_process';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { basename, dirname, join, resolve } from 'node:path';
import process from 'node:process';
import { pathToFileURL } from 'node:url';

const SOURCE_PATHS = [
    'tools/figma-plugin/src/assets.ts',
    'tools/figma-plugin/src/extract-model.ts',
    'tools/figma-plugin/src/serialize.ts',
];
const FIXED_EXPORTED_AT = '2026-07-22T12:00:00.000Z';

const args = parseArguments(process.argv.slice(2));
const repository = resolve(args['pulp-checkout'] ?? '');
const sourceRef = args['pulp-ref'] ?? 'HEAD';
const outputDirectory = resolve(args['output-dir'] ?? 'fixtures/design-ir');
const inputPath = resolve(args.input ?? 'fixtures/design-ir/pulp-emitter-generic.input.json');
const esbuildRoot = resolve(args['esbuild-root'] ?? repository);
if (!args['pulp-checkout']) throw new Error('--pulp-checkout is required');

const commit = git(repository, ['rev-parse', sourceRef]).trim();
const sourceBlobs = Object.fromEntries(SOURCE_PATHS.map((path) => {
    const line = git(repository, ['ls-tree', commit, path]).trim();
    const match = /^100644 blob ([0-9a-f]{40})\t/.exec(line);
    if (!match) throw new Error(`${path} is not a regular tracked file at ${commit}`);
    const worktreeBlob = git(repository, ['hash-object', path]).trim();
    if (worktreeBlob !== match[1]) {
        throw new Error(`${path} in the checkout does not match ${sourceRef}`);
    }
    return [path, match[1]];
}));

const inputBytes = await readFile(inputPath);
const temporary = await mkdtemp(join(tmpdir(), 'vellum-figma-emitter-'));
try {
    const entry = join(temporary, 'generate.ts');
    const bundle = join(temporary, 'generate.mjs');
    const serializePath = join(repository, 'tools/figma-plugin/src/serialize.ts');
    const assetsPath = join(repository, 'tools/figma-plugin/src/assets.ts');
    await writeFile(entry, emitterEntry(serializePath, assetsPath), 'utf8');
    const esbuildModule = join(
        esbuildRoot,
        'tools/figma-plugin/node_modules/esbuild/lib/main.js',
    );
    const { build } = await import(pathToFileURL(esbuildModule));
    await build({
        bundle: true,
        entryPoints: [entry],
        format: 'esm',
        platform: 'node',
        outfile: bundle,
        target: ['node20'],
    });
    const generated = spawnSync(process.execPath, [bundle], {
        encoding: 'utf8',
        env: { ...process.env, VELLUM_FIGMA_FIXTURE_INPUT: inputPath },
    });
    if (generated.status !== 0) {
        throw new Error(`Pulp emitter fixture failed: ${generated.stderr || generated.stdout}`);
    }
    const result = JSON.parse(generated.stdout);
    const fixtureBytes = Buffer.from(`${JSON.stringify(result.envelope, null, 2)}\n`, 'utf8');
    const assetBytes = Buffer.from(result.assetBase64, 'base64');
    const asset = result.envelope.asset_manifest.assets[0];
    const assetPath = join(outputDirectory, asset.local_path);
    await mkdir(dirname(assetPath), { recursive: true });
    await writeFile(join(outputDirectory, 'pulp-emitter-generic.export.json'), fixtureBytes);
    await writeFile(assetPath, assetBytes);

    const receipt = {
        asset: {
            path: asset.local_path,
            sha256: sha256(assetBytes),
        },
        emitter: {
            commit,
            repository: 'Generous-Corp/pulp',
            sourceBlobs,
            sourceRef,
        },
        fixture: {
            formatVersion: result.envelope.format_version,
            parserVersion: result.envelope.parser_version,
            path: 'pulp-emitter-generic.export.json',
            sha256: sha256(fixtureBytes),
        },
        generatedAt: FIXED_EXPORTED_AT,
        generatorInput: {
            path: basename(inputPath),
            sha256: sha256(inputBytes),
        },
        schema: 'vellum.pulp-figma-emitter-fixture-receipt.v1',
    };
    await writeFile(
        join(outputDirectory, 'pulp-emitter-generic.receipt.json'),
        `${JSON.stringify(receipt, null, 2)}\n`,
        'utf8',
    );
} finally {
    await rm(temporary, { recursive: true, force: true });
}

function emitterEntry(serializePath, assetsPath) {
    return `
import { readFile } from 'node:fs/promises';
import { AssetCache } from ${JSON.stringify(assetsPath)};
import { serializeExport } from ${JSON.stringify(serializePath)};

const input = JSON.parse(await readFile(process.env.VELLUM_FIGMA_FIXTURE_INPUT, 'utf8'));
const assetBytes = Buffer.from(input.asset.svg, 'utf8');
const assets = new AssetCache();
const captured = await assets.captureExportedNode({
    id: input.asset.figmaNodeId,
    exportAsync: async () => new Uint8Array(assetBytes),
}, 'SVG');
if (!('assetId' in captured)) throw new Error(captured.error);
input.root.children.find((node) => node.figma_node_id === input.asset.figmaNodeId).asset_ref = captured.assetId;
const RealDate = Date;
globalThis.Date = class FixedDate extends RealDate {
    constructor(...values) {
        super(...(values.length === 0 ? [${JSON.stringify(FIXED_EXPORTED_AT)}] : values));
    }
    static now() { return new RealDate(${JSON.stringify(FIXED_EXPORTED_AT)}).valueOf(); }
};
const envelope = serializeExport([input.root], [], {
    ...input.context,
    assets,
    fontFamilyAssets: [],
});
process.stdout.write(JSON.stringify({ assetBase64: assetBytes.toString('base64'), envelope }));
`;
}

function sha256(bytes) {
    return createHash('sha256').update(bytes).digest('hex');
}

function git(repository, arguments_) {
    return execFileSync('git', ['-C', repository, ...arguments_], { encoding: 'utf8' });
}

function parseArguments(values) {
    const output = {};
    for (let index = 0; index < values.length; index += 1) {
        const argument = values[index];
        if (!argument.startsWith('--')) throw new Error(`unexpected argument '${argument}'`);
        const key = argument.slice(2);
        if (index + 1 >= values.length) throw new Error(`missing value for --${key}`);
        output[key] = values[++index];
    }
    return output;
}
