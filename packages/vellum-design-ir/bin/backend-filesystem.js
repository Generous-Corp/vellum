import {
    copyFile,
    lstat,
    mkdir,
    readFile,
    readdir,
    rename,
    rm,
    stat,
    writeFile,
} from 'node:fs/promises';
import { dirname, isAbsolute, join, relative, resolve, sep } from 'node:path';
import process from 'node:process';
import {
    emptyAuthoredOverlay,
    parseAuthoredOverlay,
} from '../src/index.js';
import {
    fail,
    IMPORT_GRAPH_SCHEMA,
    sha256,
} from './backend-protocol.js';

export function projectPaths(project, sourceKey, revision) {
    const snapshotRoot = join(project, 'sources', 'imported', sourceKey, revision);
    return {
        aggregateIr: join(project, 'design', 'ir', 'app.designir.json'),
        assetManifest: join(project, 'assets', 'generated', sourceKey, 'manifest.json'),
        generatedAssets: join(project, 'assets', 'generated', sourceKey, 'files'),
        generatedBindings: join(project, 'ui', 'generated', `${sourceKey}.bindings.json`),
        generatedComponents: join(project, 'design', 'generated', `${sourceKey}.components.json`),
        importGraph: join(project, 'design', 'imports.json'),
        importLock: join(project, 'design', 'import.lock.json'),
        importReport: join(project, 'design', 'reports', `${sourceKey}.import-report.json`),
        importedTokens: join(project, 'tokens', 'imported', `${sourceKey}.tokens.json`),
        materializedUi: join(project, 'ui', 'generated', `${sourceKey}.materialized.json`),
        nodeIds: join(project, 'design', 'generated', 'node-ids.d.ts'),
        overlay: join(project, 'design', 'overlays', `${sourceKey}.authored.json`),
        pendingIr: join(project, 'design', 'reports', `${sourceKey}.${revision}.candidate.designir.json`),
        project,
        reimportReport: join(project, 'design', 'reports', `${sourceKey}.${revision}.reimport-report.json`),
        resolvedTokenLayers: join(project, 'tokens', 'generated', `${sourceKey}.layers.json`),
        snapshotAssets: snapshotRoot,
        snapshotArchive: join(snapshotRoot, 'source.pulp.zip'),
        snapshotCapture: join(snapshotRoot, 'capture.json'),
        snapshotProvenance: join(snapshotRoot, 'provenance.json'),
        snapshotSource: join(snapshotRoot, 'source.json'),
        sourceIr: join(project, 'design', 'ir', 'sources', `${sourceKey}.designir.json`),
    };
}

export async function validateProject(project) {
    const root = await stat(project).catch(() => null);
    if (!root?.isDirectory()) fail('project_not_found', `Project directory does not exist: ${project}`);
    const lock = await readRequiredJson(join(project, 'framework.lock'), 'project lock');
    if (lock.schema !== 'vellum.project-lock.v1') {
        fail('invalid_project_lock', `Unsupported project lock schema '${lock.schema ?? ''}'`);
    }
}

export async function loadOrCreateOverlay(path, sourceKey) {
    try {
        const bytes = await readFile(path);
        return { created: false, value: parseAuthoredOverlay(bytes.toString('utf8')) };
    } catch (error) {
        if (error.code !== 'ENOENT') throw error;
        return { created: true, value: emptyAuthoredOverlay(sourceKey) };
    }
}

export async function compareGeneratedFiles(project, expected) {
    const changes = [];
    for (const [path, expectedBytes] of [...expected.entries()].sort(([left], [right]) =>
        left.localeCompare(right))) {
        await assertSafeOutputPath(project, path);
        const metadata = await lstat(path).catch((error) => {
            if (error.code === 'ENOENT') return null;
            throw error;
        });
        if (metadata === null) {
            changes.push(await generatedChange(project, path, expectedBytes, 'missing'));
            continue;
        }
        if (metadata.isSymbolicLink() || !metadata.isFile()) {
            fail('unsafe_generated_output', `Generated output is not a regular file: ${path}`);
        }
        const actual = await readFile(path);
        if (!actual.equals(expectedBytes)) {
            changes.push(await generatedChange(project, path, expectedBytes, 'modified', actual));
        }
    }
    return changes;
}

export async function generatedChange(project, path, expected, kind, actualValue = undefined) {
    const actual = actualValue === undefined
        ? await readFile(path).catch((error) => {
            if (error.code === 'ENOENT') return null;
            throw error;
        })
        : actualValue;
    return {
        actualBytes: actual?.length ?? null,
        actualSha256: actual === null ? null : `sha256:${sha256(actual)}`,
        expectedBytes: expected?.length ?? null,
        expectedSha256: expected === null ? null : `sha256:${sha256(expected)}`,
        kind,
        path: relative(project, path),
    };
}

export async function writeTransaction(project, files, options = {}) {
    const entries = [...files.entries()].sort(([left], [right]) => left.localeCompare(right));
    const preconditions = options.preconditions ?? new Map();
    if (!(preconditions instanceof Map)) {
        fail('invalid_transaction', 'Transaction preconditions must be a map');
    }
    for (const [path] of entries) await assertSafeOutputPath(project, path);
    for (const path of options.removals ?? []) await assertSafeOutputPath(project, path);
    for (const [path] of preconditions) await assertSafeOutputPath(project, path);
    await assertSafeOutputPath(project, join(project, '.vellum', 'transactions', '.probe'));
    const transaction = join(project, '.vellum', 'transactions', `${process.pid}-${sha256(Buffer.from(entries.map(([path]) => path).join('\n'))).slice(0, 12)}`);
    await rm(transaction, { force: true, recursive: true });
    const staged = join(transaction, 'staged');
    const backups = join(transaction, 'backups');
    await mkdir(staged, { recursive: true });
    const touched = [];
    try {
        for (const [path, bytes] of entries) {
            const relativePath = relative(project, path);
            const stagedPath = join(staged, relativePath);
            await mkdir(dirname(stagedPath), { recursive: true });
            await writeFile(stagedPath, bytes);
        }
        for (const [path, expected] of preconditions) {
            await assertBytesUnchanged(path, expected);
        }
        for (const [path] of entries) {
            const relativePath = relative(project, path);
            const backupPath = join(backups, relativePath);
            const exists = await lstat(path).catch(() => null);
            if (exists) {
                if (!exists.isFile()) fail('unsafe_output_path', `Output path is not a file: ${path}`);
                await mkdir(dirname(backupPath), { recursive: true });
                await copyFile(path, backupPath);
            }
            await mkdir(dirname(path), { recursive: true });
            await rename(join(staged, relativePath), path);
            touched.push({ backupPath, existed: Boolean(exists), path });
        }
        for (const path of options.removals ?? []) {
            const exists = await lstat(path).catch(() => null);
            if (!exists) continue;
            const relativePath = relative(project, path);
            const backupPath = join(backups, relativePath);
            await mkdir(dirname(backupPath), { recursive: true });
            await copyFile(path, backupPath);
            await rm(path);
            touched.push({ backupPath, existed: true, path });
        }
        for (const [path, expected] of preconditions) {
            await assertBytesUnchanged(path, expected);
        }
    } catch (error) {
        for (const item of touched.reverse()) {
            if (item.existed) {
                await mkdir(dirname(item.path), { recursive: true });
                await copyFile(item.backupPath, item.path);
            } else {
                await rm(item.path, { force: true });
            }
        }
        throw error;
    } finally {
        await rm(transaction, { force: true, recursive: true });
    }
}

export async function assertImmutableSnapshot(path, expected) {
    const existing = await readFile(path).catch((error) => {
        if (error.code === 'ENOENT') return null;
        throw error;
    });
    if (existing !== null && !existing.equals(expected)) {
        fail('immutable_snapshot_conflict', `Snapshot already exists with different bytes: ${path}`);
    }
}

export async function assertBytesUnchanged(path, expected) {
    const actual = await readFile(path);
    if (!actual.equals(expected)) fail('authored_file_changed', `Authored file changed: ${path}`);
}

export async function readRequiredJson(path, label) {
    try {
        return JSON.parse(await readFile(path, 'utf8'));
    } catch (error) {
        fail('invalid_json', `Cannot read ${label} at ${path}: ${error.message}`);
    }
}

export async function readOptionalJson(path) {
    try {
        return JSON.parse(await readFile(path, 'utf8'));
    } catch (error) {
        if (error.code === 'ENOENT') return null;
        fail('invalid_json', `Cannot read JSON at ${path}: ${error.message}`);
    }
}

export async function staleGeneratedAssets(root, copies) {
    const retained = new Set(copies.map((copy) => resolve(copy.generatedPath)));
    const stale = [];
    async function visit(directory) {
        const metadata = await lstat(directory).catch((error) => {
            if (error.code === 'ENOENT') return null;
            throw error;
        });
        if (metadata === null) return;
        if (metadata.isSymbolicLink() || !metadata.isDirectory()) {
            fail('unsafe_output_path', `Generated asset directory is unsafe: ${directory}`);
        }
        const entries = await readdir(directory, { withFileTypes: true });
        for (const entry of entries.sort((left, right) => left.name.localeCompare(right.name))) {
            const path = join(directory, entry.name);
            if (entry.isSymbolicLink()) fail('unsafe_output_path', `Generated asset is a symlink: ${path}`);
            if (entry.isDirectory()) await visit(path);
            else if (entry.isFile() && !retained.has(resolve(path))) stale.push(path);
            else if (!entry.isFile()) fail('unsafe_output_path', `Generated asset is not a regular file: ${path}`);
        }
    }
    await visit(root);
    return stale.sort();
}

export async function assertSafeOutputPath(project, path) {
    assertInside(project, path);
    const segments = relative(project, dirname(path)).split(sep).filter(Boolean);
    let current = project;
    for (const segment of segments) {
        current = join(current, segment);
        const metadata = await lstat(current).catch((error) => {
            if (error.code === 'ENOENT') return null;
            throw error;
        });
        if (metadata === null) break;
        if (metadata.isSymbolicLink() || !metadata.isDirectory()) {
            fail('unsafe_output_path', `Output parent is not a real directory: ${current}`);
        }
    }
    const target = await lstat(path).catch((error) => {
        if (error.code === 'ENOENT') return null;
        throw error;
    });
    if (target && (target.isSymbolicLink() || !target.isFile())) {
        fail('unsafe_output_path', `Output is not a regular file: ${path}`);
    }
}

export function validateImportGraph(graph, sourceKey, lockedSource) {
    if (
        graph?.schema !== IMPORT_GRAPH_SCHEMA ||
        !Array.isArray(graph.order) ||
        !graph.order.includes(sourceKey) ||
        graph.sources?.[sourceKey]?.adapter !== lockedSource.adapter ||
        graph.sources?.[sourceKey]?.namespace !== lockedSource.namespace
    ) {
        fail('invalid_import_graph', `design/imports.json no longer matches source '${sourceKey}'`);
    }
}

export function relativeFiles(project, files) {
    return [...files.keys()].map((path) => relative(project, path)).sort();
}

function assertInside(project, path) {
    const output = relative(project, path);
    if (!output || output.startsWith(`..${sep}`) || output === '..' || isAbsolute(output)) {
        fail('unsafe_output_path', `Refusing output outside the project: ${path}`);
    }
}
