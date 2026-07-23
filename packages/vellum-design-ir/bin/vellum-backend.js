#!/usr/bin/env node

import { createHash } from 'node:crypto';
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
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from 'node:path';
import process from 'node:process';
import {
    applyAuthoredOverlay,
    decodeFigmaPluginExport,
    emptyAuthoredOverlay,
    indexTree,
    normalizeImport,
    parseAuthoredOverlay,
    parseDesignIR,
    reimportDesign,
    normalizeSha256ContentHash,
    stableStringify,
    summarizeDesignIR,
} from '../src/index.js';

const BACKEND_SCHEMA = 'vellum.backend.result.v1';
const IMPORT_LOCK_SCHEMA = 'vellum.design-import-lock.v1';
const IMPORT_GRAPH_SCHEMA = 'vellum.design-imports.v1';
const IMPORT_REPORT_SCHEMA = 'vellum.design-import-report.v1';
const ASSET_MANIFEST_SCHEMA = 'vellum.design-assets.v1';
const GENERATED_COMPONENT_SCHEMA = 'vellum.generated-components.v1';
const MAX_SOURCE_BYTES = 16 * 1024 * 1024;
const MAX_ARCHIVE_BYTES = 256 * 1024 * 1024;
const MAX_ASSET_BYTES = 128 * 1024 * 1024;
const MAX_ASSET_COUNT = 10_000;
const MAX_NODE_COUNT = 100_000;
const MAX_TREE_DEPTH = 512;

const [command, ...rawArguments] = process.argv.slice(2);

try {
    const args = parseArguments(rawArguments);
    if (!args.project) fail('invalid_arguments', '--project is required');
    if (!args.json) fail('invalid_arguments', '--json is required by the backend protocol');
    const project = resolve(args.project);
    await validateProject(project);
    let payload;
    if (command === 'import') payload = await importDesign(project, args);
    else if (command === 'reimport') payload = await reimportDesignFilesystem(project, args);
    else if (['build', 'run', 'test', 'capture', 'package'].includes(command)) {
        fail(
            'capability_unavailable',
            `The installed backend does not yet provide the '${command}' capability`,
            { exitCode: 4 },
        );
    }
    else fail('unsupported_command', `vellum-backend does not implement '${command ?? ''}'`);
    process.stdout.write(stableStringify(payload, { space: 0 }));
} catch (error) {
    const status = error.status ?? 'backend_error';
    const diagnostics = Array.isArray(error.diagnostics) ? error.diagnostics : [];
    process.stdout.write(stableStringify({
        data: {},
        diagnostics,
        message: error.message,
        ok: false,
        schema: BACKEND_SCHEMA,
        status,
    }, { space: 0 }));
    process.exitCode = Number.isInteger(error.exitCode) ? error.exitCode : 1;
}

async function importDesign(project, args) {
    const sourcePath = requiredSource(args);
    const sourceKey = validateSourceKey(args['source-key'] ?? 'main');
    const sourceType = args['source-type'] ?? 'figma';
    const loaded = await loadSource(sourcePath, {
        sourceArchive: sourceArchiveArguments(args),
        sourceKey,
        sourceType,
    });
    const paths = projectPaths(project, sourceKey, loaded.revision);
    const importLock = await readOptionalJson(paths.importLock);
    if (importLock?.sources?.[sourceKey]) {
        fail('source_already_imported', `Source '${sourceKey}' already exists; use vellum reimport`);
    }
    if (importLock && Object.keys(importLock.sources ?? {}).length > 0) {
        fail(
            'multi_source_not_implemented',
            'This experimental backend currently supports one imported source per application',
        );
    }

    const overlay = await loadOrCreateOverlay(paths.overlay, sourceKey);
    const applied = applyAuthoredOverlay(loaded.document, overlay.value);
    if (applied.conflicts.length > 0) {
        fail('authored_overlay_conflict', 'The authored overlay does not resolve against this import', {
            diagnostics: conflictsAsDiagnostics(applied.conflicts),
        });
    }
    const assets = await planAssets(sourcePath, loaded.document, paths);
    const files = generatedFiles({
        applied,
        assets,
        document: loaded.document,
        overlayCreated: overlay.created,
        overlayValue: overlay.value,
        paths,
        project,
        sourceHash: loaded.sourceHash,
        sourceKey,
        sourceArtifact: loaded.sourceArtifact,
        sourceName: loaded.sourceName,
        sourceType,
    });
    await assertImmutableSnapshot(paths.snapshotSource, loaded.sourceBytes);
    files.set(paths.snapshotSource, loaded.sourceBytes);
    if (loaded.archiveBytes) {
        await assertImmutableSnapshot(paths.snapshotArchive, loaded.archiveBytes);
        files.set(paths.snapshotArchive, loaded.archiveBytes);
    }
    await assertImmutableSnapshot(paths.snapshotProvenance, files.get(paths.snapshotProvenance));
    for (const asset of assets.copies) {
        await assertImmutableSnapshot(asset.snapshotPath, asset.bytes);
        files.set(asset.snapshotPath, asset.bytes);
        files.set(asset.generatedPath, asset.bytes);
    }
    await writeTransaction(project, files);

    return success('imported', `Imported '${sourceKey}' revision '${loaded.revision}'`, {
        activeRevision: loaded.revision,
        files: relativeFiles(project, files),
        report: relative(project, paths.importReport),
        sourceKey,
        summary: summarizeDesignIR(loaded.document),
    }, [...loaded.backendDiagnostics, ...assets.diagnostics]);
}

async function reimportDesignFilesystem(project, args) {
    const sourcePath = requiredSource(args);
    const sourceKey = validateSourceKey(args['source-key'] ?? 'main');
    const pathsForLock = projectPaths(project, sourceKey, 'pending');
    const importLock = await readRequiredJson(pathsForLock.importLock, 'design import lock');
    if (importLock.schema !== IMPORT_LOCK_SCHEMA) {
        fail('invalid_import_lock', `Unsupported import lock schema '${importLock.schema ?? ''}'`);
    }
    const lockedSource = importLock.sources?.[sourceKey];
    if (!lockedSource) fail('source_not_imported', `Source '${sourceKey}' has not been imported`);
    const loaded = await loadSource(sourcePath, {
        sourceArchive: sourceArchiveArguments(args),
        sourceKey,
        sourceType: lockedSource.adapter,
    });
    if ((lockedSource.sourceArtifactKind ?? 'json') !== loaded.sourceArtifact.kind) {
        fail(
            'source_artifact_kind_mismatch',
            `Source '${sourceKey}' must remain a '${lockedSource.sourceArtifactKind ?? 'json'}' artifact`,
        );
    }
    const paths = projectPaths(project, sourceKey, loaded.revision);
    const previous = parseDesignIR(await readFile(paths.sourceIr, 'utf8'));
    const importGraphBytesBefore = await readFile(paths.importGraph);
    validateImportGraph(
        JSON.parse(importGraphBytesBefore.toString('utf8')),
        sourceKey,
        lockedSource,
    );
    const overlayBytesBefore = await readFile(paths.overlay);
    const overlay = parseAuthoredOverlay(overlayBytesBefore.toString('utf8'));
    if (
        lockedSource.activeRevision === loaded.revision &&
        lockedSource.snapshotHash === loaded.sourceHash
    ) {
        // The source JSON being byte-identical does not prove that sibling
        // asset bytes still match their declared content hashes. Re-run the
        // same asset plan used by import before taking the unchanged fast path.
        const assets = await planAssets(sourcePath, loaded.document, paths);
        await assertImmutableSnapshot(paths.snapshotSource, loaded.sourceBytes);
        if (loaded.archiveBytes) {
            await assertImmutableSnapshot(paths.snapshotArchive, loaded.archiveBytes);
        }
        const applied = applyAuthoredOverlay(previous, overlay);
        if (applied.conflicts.length > 0) {
            return failure(
                'reimport_conflict',
                'The unchanged source still has authored overlay conflicts',
                {
                    activeRevision: previous.source.revision,
                    files: [],
                    sourceKey,
                },
                conflictsAsDiagnostics(applied.conflicts),
                2,
            );
        }
        await assertBytesUnchanged(paths.overlay, overlayBytesBefore);
        await assertBytesUnchanged(paths.importGraph, importGraphBytesBefore);
        return success('reimport_unchanged', `Source '${sourceKey}' is already at revision '${loaded.revision}'`, {
            activeRevision: loaded.revision,
            files: [],
            sourceKey,
            summary: summarizeDesignIR(previous),
        }, [...loaded.backendDiagnostics, ...assets.diagnostics]);
    }
    const result = reimportDesign(previous, loaded.document, overlay);
    const assets = await planAssets(sourcePath, loaded.document, paths);

    await assertImmutableSnapshot(paths.snapshotSource, loaded.sourceBytes);
    const reviewFiles = new Map([
        [paths.snapshotSource, loaded.sourceBytes],
        [paths.snapshotProvenance, jsonBytes(snapshotProvenance({
            assets: assets.manifest,
            revision: loaded.revision,
            sourceHash: loaded.sourceHash,
            sourceKey,
            sourceArtifact: loaded.sourceArtifact,
            sourceName: loaded.sourceName,
            sourceType: lockedSource.adapter,
        }))],
        [paths.reimportReport, jsonBytes(result.report)],
    ]);
    await assertImmutableSnapshot(
        paths.snapshotProvenance,
        reviewFiles.get(paths.snapshotProvenance),
    );
    if (loaded.archiveBytes) {
        await assertImmutableSnapshot(paths.snapshotArchive, loaded.archiveBytes);
        reviewFiles.set(paths.snapshotArchive, loaded.archiveBytes);
    }
    for (const asset of assets.copies) {
        await assertImmutableSnapshot(asset.snapshotPath, asset.bytes);
        reviewFiles.set(asset.snapshotPath, asset.bytes);
    }

    if (!result.accepted) {
        reviewFiles.set(paths.pendingIr, jsonBytes(loaded.document));
        await writeTransaction(project, reviewFiles);
        await assertBytesUnchanged(paths.overlay, overlayBytesBefore);
        await assertBytesUnchanged(paths.importGraph, importGraphBytesBefore);
        return failure('reimport_conflict', 'Reimport requires authored conflict resolution', {
            activeRevision: previous.source.revision,
            candidateRevision: loaded.revision,
            files: relativeFiles(project, reviewFiles),
            report: relative(project, paths.reimportReport),
            sourceKey,
        }, conflictsAsDiagnostics(result.report.conflicts), 2);
    }

    const generated = generatedFiles({
        applied: {
            materialized: result.materialized,
            resolvedBindings: result.resolvedBindings,
            tokenLayers: result.tokenLayers,
        },
        assets,
        document: loaded.document,
        overlayCreated: false,
        overlayValue: overlay,
        paths,
        project,
        sourceHash: loaded.sourceHash,
        sourceKey,
        sourceArtifact: loaded.sourceArtifact,
        sourceName: loaded.sourceName,
        sourceType: lockedSource.adapter,
    });
    generated.delete(paths.importReport);
    generated.delete(paths.importGraph);
    generated.set(paths.reimportReport, jsonBytes(result.report));
    generated.set(paths.snapshotSource, loaded.sourceBytes);
    for (const [path, bytes] of reviewFiles) generated.set(path, bytes);
    generated.delete(paths.pendingIr);
    for (const asset of assets.copies) generated.set(asset.generatedPath, asset.bytes);
    const staleAssets = await staleGeneratedAssets(paths.generatedAssets, assets.copies);
    await writeTransaction(project, generated, {
        removals: [paths.pendingIr, ...staleAssets],
    });
    await assertBytesUnchanged(paths.overlay, overlayBytesBefore);
    await assertBytesUnchanged(paths.importGraph, importGraphBytesBefore);

    return success('reimported', `Reimported '${sourceKey}' at revision '${loaded.revision}'`, {
        activeRevision: loaded.revision,
        files: relativeFiles(project, generated),
        report: relative(project, paths.reimportReport),
        sourceKey,
        summary: result.report.summary,
    }, [...loaded.backendDiagnostics, ...assets.diagnostics]);
}

function generatedFiles(context) {
    const {
        applied,
        assets,
        document,
        overlayCreated,
        overlayValue,
        paths,
        project,
        sourceHash,
        sourceKey,
        sourceArtifact,
        sourceName,
        sourceType,
    } = context;
    const files = new Map();
    const componentDefinitions = flattenComponents(document);
    const importGraph = {
        order: [sourceKey],
        schema: IMPORT_GRAPH_SCHEMA,
        sources: {
            [sourceKey]: {
                adapter: sourceType,
                namespace: document.source.namespace,
            },
        },
    };
    const lock = {
        graphVersion: 1,
        schema: IMPORT_LOCK_SCHEMA,
        sources: {
            [sourceKey]: {
                activeRevision: document.source.revision,
                adapter: sourceType,
                adapterVersion: document.source.adapterVersion,
                designIrSchema: document.$schema,
                formatVersion: document.source.formatVersion,
                namespace: document.source.namespace,
                snapshotHash: sourceHash,
                sourceArtifactKind: sourceArtifact.kind,
            },
        },
    };
    const importReport = {
        diagnostics: [...document.diagnostics, ...assets.diagnostics],
        files: {
            aggregateDesignIr: relative(project, paths.aggregateIr),
            assetManifest: relative(project, paths.assetManifest),
            generatedComponents: relative(project, paths.generatedComponents),
            materializedUi: relative(project, paths.materializedUi),
            nodeIds: relative(project, paths.nodeIds),
            normalizedDesignIr: relative(project, paths.sourceIr),
            overlay: relative(project, paths.overlay),
            snapshot: relative(project, paths.snapshotSource),
            sourceArtifact: relative(
                project,
                sourceArtifact.kind === 'pulp-zip' ? paths.snapshotArchive : paths.snapshotSource,
            ),
            tokens: relative(project, paths.importedTokens),
        },
        lossReport: document.lossReport,
        revision: document.source.revision,
        schema: IMPORT_REPORT_SCHEMA,
        source: {
            adapter: sourceType,
            key: sourceKey,
            name: sourceName,
            snapshotHash: sourceHash,
            sourceArtifact,
        },
        summary: summarizeDesignIR(document),
    };

    files.set(paths.importGraph, jsonBytes(importGraph));
    files.set(paths.importLock, jsonBytes(lock));
    files.set(paths.sourceIr, jsonBytes(document));
    files.set(paths.aggregateIr, jsonBytes(document));
    files.set(paths.generatedComponents, jsonBytes({
        components: componentDefinitions,
        revision: document.source.revision,
        schema: GENERATED_COMPONENT_SCHEMA,
        sourceKey,
    }));
    files.set(paths.nodeIds, Buffer.from(nodeIdsTypescript(componentDefinitions), 'utf8'));
    files.set(paths.importedTokens, jsonBytes(document.tokens));
    files.set(paths.assetManifest, jsonBytes({
        assets: assets.manifest,
        revision: document.source.revision,
        schema: ASSET_MANIFEST_SCHEMA,
        sourceKey,
    }));
    files.set(paths.materializedUi, jsonBytes(applied.materialized));
    files.set(paths.generatedBindings, jsonBytes({
        bindings: applied.resolvedBindings,
        sourceKey,
    }));
    files.set(paths.importReport, jsonBytes(importReport));
    files.set(paths.snapshotProvenance, jsonBytes(snapshotProvenance({
        assets: assets.manifest,
        revision: document.source.revision,
        sourceHash,
        sourceKey,
        sourceArtifact,
        sourceName,
        sourceType,
    })));
    if (overlayCreated) files.set(paths.overlay, jsonBytes(overlayValue));
    return files;
}

async function loadSource(sourcePath, { sourceArchive, sourceKey, sourceType }) {
    if (!['figma', 'design-ir'].includes(sourceType)) {
        fail('unsupported_source_type', `Unsupported source type '${sourceType}'`);
    }
    const file = resolve(sourcePath);
    const metadata = await lstat(file).catch(() => null);
    if (!metadata?.isFile() || metadata.isSymbolicLink()) {
        fail('invalid_source', `Import source must be a regular file: ${file}`);
    }
    if (metadata.size > MAX_SOURCE_BYTES) {
        fail('source_too_large', `Import source exceeds ${MAX_SOURCE_BYTES} bytes`);
    }
    const sourceBytes = await readFile(file);
    if (sourceArchive === null && (
        basename(file).toLowerCase().endsWith('.pulp.zip') || hasZipMagic(sourceBytes)
    )) {
        fail(
            'source_archive_requires_dispatcher',
            'Pulp ZIP sources must enter through the installed vellum CLI dispatcher',
        );
    }
    const archive = await loadStagedSourceArchive(sourceArchive, sourceType);
    let input;
    try {
        input = JSON.parse(sourceBytes.toString('utf8'));
    } catch (error) {
        fail('invalid_source_json', `Import source is not valid JSON: ${error.message}`);
    }
    if (archive && !(
        input?.$schema === 'https://pulp.dev/schemas/figma-plugin-export-v1.json' &&
        input?.format_version === '2026.05-figma-plugin-v1'
    )) {
        fail(
            'invalid_source_archive',
            'Pulp ZIP scene must use the pinned Figma plugin export contract',
        );
    }
    const sceneHash = `sha256:${sha256(sourceBytes)}`;
    const sourceHash = archive?.sha256 ?? sceneHash;
    enforceSourceLimits(input);
    let document;
    if (input?.$schema === 'https://vellum.dev/schemas/design-ir/v1') {
        document = parseDesignIR(input);
        if (document.source.key !== sourceKey) {
            fail(
                'source_key_mismatch',
                `Canonical DesignIR source key '${document.source.key}' does not match '${sourceKey}'`,
            );
        }
    } else {
        if (sourceType === 'figma' && input?.format_version === '2026.05-figma-plugin-v1') {
            input = decodeFigmaPluginExport(input, { sourceHash, sourceKey });
        }
        if (!input?.source || !input?.root) {
            fail(
                'unsupported_source_contract',
                'This backend accepts a generic Figma plugin export, Vellum adapter source model, or canonical DesignIR JSON',
            );
        }
        const normalizedInput = structuredClone(input);
        normalizedInput.source.key = sourceKey;
        normalizedInput.source.namespace = sourceKey;
        normalizedInput.source.snapshotHash = sourceHash;
        if (!normalizedInput.source.adapter) normalizedInput.source.adapter = sourceType;
        document = normalizeImport(normalizedInput);
    }
    const revision = validateRevision(document.source.revision);
    const sourceArtifact = archive ? {
        kind: 'pulp-zip',
        member: archive.member,
        name: archive.name,
        sceneSha256: sceneHash,
        sha256: archive.sha256,
    } : {
        kind: 'json',
        name: basename(file),
        sha256: sceneHash,
    };
    return {
        archiveBytes: archive?.bytes ?? null,
        backendDiagnostics: [],
        document,
        revision,
        sourceArtifact,
        sourceBytes,
        sourceHash,
        sourceName: sourceArtifact.name,
    };
}

function hasZipMagic(bytes) {
    if (bytes.length < 4 || bytes[0] !== 0x50 || bytes[1] !== 0x4b) return false;
    return (
        (bytes[2] === 0x03 && bytes[3] === 0x04) ||
        (bytes[2] === 0x05 && bytes[3] === 0x06) ||
        (bytes[2] === 0x07 && bytes[3] === 0x08)
    );
}

function sourceArchiveArguments(args) {
    const values = {
        member: args['source-archive-member'],
        name: args['source-archive-name'],
        path: args['source-archive'],
        sha256: args['source-archive-sha256'],
    };
    const present = Object.values(values).filter((value) => value !== undefined).length;
    if (present === 0) return null;
    if (present !== Object.keys(values).length) {
        fail('invalid_source_archive', 'Staged Pulp archive metadata is incomplete');
    }
    return values;
}

async function loadStagedSourceArchive(value, sourceType) {
    if (value === null) return null;
    if (sourceType !== 'figma') {
        fail('invalid_source_archive', 'Pulp ZIP sources require the figma source type');
    }
    if (
        typeof value.name !== 'string' || !value.name ||
        basename(value.name) !== value.name || value.name.includes('\\')
    ) {
        fail('invalid_source_archive', 'Staged Pulp archive name is unsafe');
    }
    if (!isSafeRelativeAsset(value.member) || !value.member.toLowerCase().endsWith('.pulp.json')) {
        fail('invalid_source_archive', 'Staged Pulp archive scene member is unsafe');
    }
    let expectedHash;
    try {
        expectedHash = normalizeSha256ContentHash(value.sha256, 'source archive SHA-256');
    } catch (error) {
        fail('invalid_source_archive', error.message);
    }
    const archivePath = resolve(value.path);
    const metadata = await lstat(archivePath).catch(() => null);
    if (!metadata?.isFile() || metadata.isSymbolicLink()) {
        fail('invalid_source_archive', 'Staged Pulp archive must be a regular file');
    }
    if (metadata.size > MAX_ARCHIVE_BYTES) {
        fail('source_archive_too_large', `Pulp source archive exceeds ${MAX_ARCHIVE_BYTES} bytes`);
    }
    const bytes = await readFile(archivePath);
    const actualHash = `sha256:${sha256(bytes)}`;
    if (actualHash !== expectedHash) {
        fail('source_archive_mutated', 'Staged Pulp archive bytes do not match the dispatcher receipt');
    }
    if (bytes.length < 4 || bytes[0] !== 0x50 || bytes[1] !== 0x4b) {
        fail('invalid_source_archive', 'Staged Pulp source is not a ZIP archive');
    }
    return { bytes, member: value.member, name: value.name, sha256: actualHash };
}

async function planAssets(sourcePath, document, paths) {
    const manifest = [];
    const copies = [];
    const diagnostics = [];
    for (const asset of document.assets) {
        const receipt = { ...asset, status: 'declared' };
        if (typeof asset.uri === 'string' && isSafeRelativeAsset(asset.uri)) {
            const sourceAsset = resolve(dirname(resolve(sourcePath)), asset.uri);
            await assertSafeSourceAsset(dirname(resolve(sourcePath)), sourceAsset);
            const metadata = await lstat(sourceAsset).catch(() => null);
            if (metadata?.isFile() && !metadata.isSymbolicLink()) {
                if (metadata.size > MAX_ASSET_BYTES) {
                    fail('asset_too_large', `Asset '${asset.id}' exceeds ${MAX_ASSET_BYTES} bytes`);
                }
                const bytes = await readFile(sourceAsset);
                const digest = `sha256:${sha256(bytes)}`;
                let declaredHash;
                if (asset.contentHash !== undefined) {
                    try {
                        declaredHash = normalizeSha256ContentHash(
                            asset.contentHash,
                            `assets.${asset.id}.contentHash`,
                        );
                    } catch (error) {
                        fail('invalid_asset_hash', error.message);
                    }
                }
                if (declaredHash !== undefined && declaredHash !== digest) {
                    fail('asset_hash_mismatch', `Asset '${asset.id}' does not match its declared hash`);
                }
                const relativeAsset = normalizeAssetPath(asset.uri);
                copies.push({
                    bytes,
                    generatedPath: join(paths.generatedAssets, relativeAsset),
                    snapshotPath: join(paths.snapshotAssets, relativeAsset),
                });
                receipt.contentHash = digest;
                receipt.generatedPath = relative(paths.project, join(paths.generatedAssets, relativeAsset));
                receipt.status = 'copied';
            } else {
                receipt.status = 'missing';
                diagnostics.push({
                    code: 'source-asset-missing',
                    disposition: 'unsupported',
                    level: 'warning',
                    message: `Declared asset '${asset.id}' was not present beside the source snapshot`,
                    path: `assets.${asset.id}`,
                });
            }
        } else if (asset.uri !== undefined) {
            fail('unsafe_asset_uri', `Asset '${asset.id}' has an unsafe or unsupported URI`);
        }
        manifest.push(receipt);
    }
    manifest.sort((left, right) => left.id.localeCompare(right.id));
    return { copies, diagnostics, manifest };
}

function projectPaths(project, sourceKey, revision) {
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
        snapshotAssets: snapshotRoot,
        snapshotArchive: join(snapshotRoot, 'source.pulp.zip'),
        snapshotProvenance: join(snapshotRoot, 'provenance.json'),
        snapshotSource: join(snapshotRoot, 'source.json'),
        sourceIr: join(project, 'design', 'ir', 'sources', `${sourceKey}.designir.json`),
    };
}

async function loadOrCreateOverlay(path, sourceKey) {
    try {
        const bytes = await readFile(path);
        return { created: false, value: parseAuthoredOverlay(bytes.toString('utf8')) };
    } catch (error) {
        if (error.code !== 'ENOENT') throw error;
        return { created: true, value: emptyAuthoredOverlay(sourceKey) };
    }
}

async function validateProject(project) {
    const root = await stat(project).catch(() => null);
    if (!root?.isDirectory()) fail('project_not_found', `Project directory does not exist: ${project}`);
    const lock = await readRequiredJson(join(project, 'framework.lock'), 'project lock');
    if (lock.schema !== 'vellum.project-lock.v1') {
        fail('invalid_project_lock', `Unsupported project lock schema '${lock.schema ?? ''}'`);
    }
}

function requiredSource(args) {
    const value = args.source ?? args._?.[0];
    if (!value) fail('invalid_arguments', '--source (or import positional source) is required');
    return value;
}

function parseArguments(values) {
    const output = { _: [] };
    for (let index = 0; index < values.length; index += 1) {
        const argument = values[index];
        if (!argument.startsWith('--')) {
            output._.push(argument);
            continue;
        }
        const key = argument.slice(2);
        if (key === 'json') {
            output.json = true;
            continue;
        }
        const value = values[index + 1];
        if (value === undefined || value.startsWith('--')) {
            fail('invalid_arguments', `--${key} requires a value`);
        }
        if (Object.hasOwn(output, key)) fail('invalid_arguments', `--${key} was provided twice`);
        output[key] = value;
        index += 1;
    }
    return output;
}

function flattenComponents(document) {
    const { index } = indexTree(document.root);
    return [...index.values()]
        .map(({ childIndex, node, parentId }) => ({
            childIds: node.children.map((child) => child.id),
            childIndex,
            id: node.id,
            kind: node.kind,
            name: node.name ?? null,
            parentId,
            role: node.role ?? null,
        }))
        .sort((left, right) => left.id.localeCompare(right.id));
}

function nodeIdsTypescript(components) {
    const ids = components.map((component) => JSON.stringify(component.id));
    return [
        '// Generated by vellum-backend. Do not edit.',
        `export const importedNodeIds = [${ids.join(', ')}] as const;`,
        'export type ImportedNodeId = (typeof importedNodeIds)[number];',
        '',
    ].join('\n');
}

function snapshotProvenance({
    assets,
    revision,
    sourceArtifact,
    sourceHash,
    sourceKey,
    sourceName,
    sourceType,
}) {
    return {
        assets: assets.map((asset) => ({
            contentHash: asset.contentHash ?? null,
            id: asset.id,
            status: asset.status,
            uri: asset.uri ?? null,
        })),
        revision,
        schema: 'vellum.source-snapshot.v1',
        sourceArtifact,
        sourceHash,
        sourceKey,
        sourceName,
        sourceType,
    };
}

async function writeTransaction(project, files, options = {}) {
    const entries = [...files.entries()].sort(([left], [right]) => left.localeCompare(right));
    for (const [path] of entries) await assertSafeOutputPath(project, path);
    for (const path of options.removals ?? []) await assertSafeOutputPath(project, path);
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

async function assertImmutableSnapshot(path, expected) {
    const existing = await readFile(path).catch((error) => {
        if (error.code === 'ENOENT') return null;
        throw error;
    });
    if (existing !== null && !existing.equals(expected)) {
        fail('immutable_snapshot_conflict', `Snapshot already exists with different bytes: ${path}`);
    }
}

async function assertBytesUnchanged(path, expected) {
    const actual = await readFile(path);
    if (!actual.equals(expected)) fail('authored_file_changed', `Authored file changed: ${path}`);
}

async function readRequiredJson(path, label) {
    try {
        return JSON.parse(await readFile(path, 'utf8'));
    } catch (error) {
        fail('invalid_json', `Cannot read ${label} at ${path}: ${error.message}`);
    }
}

async function readOptionalJson(path) {
    try {
        return JSON.parse(await readFile(path, 'utf8'));
    } catch (error) {
        if (error.code === 'ENOENT') return null;
        fail('invalid_json', `Cannot read JSON at ${path}: ${error.message}`);
    }
}

function validateSourceKey(value) {
    if (!/^[a-z][a-z0-9-]{0,63}$/.test(value)) {
        fail('invalid_source_key', 'Source key must be lowercase kebab-case and at most 64 characters');
    }
    return value;
}

function validateRevision(value) {
    if (typeof value !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/.test(value)) {
        fail('invalid_revision', 'Source revision must be a safe 1-80 character identifier');
    }
    return value;
}

function isSafeRelativeAsset(value) {
    return typeof value === 'string' && value.length > 0 && !isAbsolute(value) &&
        !value.includes('\\') && !value.split('/').some((part) => ['', '.', '..'].includes(part));
}

function normalizeAssetPath(value) {
    return value.split('/').join(sep);
}

async function staleGeneratedAssets(root, copies) {
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

function validateImportGraph(graph, sourceKey, lockedSource) {
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

function enforceSourceLimits(input) {
    if (Array.isArray(input?.assets) && input.assets.length > MAX_ASSET_COUNT) {
        fail('source_limit_exceeded', `Import source exceeds ${MAX_ASSET_COUNT} assets`);
    }
    if (!input?.root || typeof input.root !== 'object') return;
    const stack = [{ depth: 0, node: input.root }];
    let count = 0;
    while (stack.length > 0) {
        const { depth, node } = stack.pop();
        count += 1;
        if (count > MAX_NODE_COUNT) {
            fail('source_limit_exceeded', `Import source exceeds ${MAX_NODE_COUNT} nodes`);
        }
        if (depth > MAX_TREE_DEPTH) {
            fail('source_limit_exceeded', `Import source exceeds tree depth ${MAX_TREE_DEPTH}`);
        }
        if (Array.isArray(node?.children)) {
            for (const child of node.children) stack.push({ depth: depth + 1, node: child });
        }
    }
}

async function assertSafeSourceAsset(root, path) {
    const relativePath = relative(root, path);
    if (!relativePath || relativePath.startsWith(`..${sep}`) || isAbsolute(relativePath)) {
        fail('unsafe_asset_uri', `Asset escapes its source snapshot: ${path}`);
    }
    let current = root;
    for (const segment of relativePath.split(sep)) {
        current = join(current, segment);
        const metadata = await lstat(current).catch((error) => {
            if (error.code === 'ENOENT') return null;
            throw error;
        });
        if (metadata === null) return;
        if (metadata.isSymbolicLink()) {
            fail('unsafe_asset_uri', `Asset path contains a symbolic link: ${current}`);
        }
    }
}

async function assertSafeOutputPath(project, path) {
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

function assertInside(project, path) {
    const output = relative(project, path);
    if (!output || output.startsWith(`..${sep}`) || output === '..' || isAbsolute(output)) {
        fail('unsafe_output_path', `Refusing output outside the project: ${path}`);
    }
}

function relativeFiles(project, files) {
    return [...files.keys()].map((path) => relative(project, path)).sort();
}

function jsonBytes(value) {
    return Buffer.from(stableStringify(value), 'utf8');
}

function sha256(bytes) {
    return createHash('sha256').update(bytes).digest('hex');
}

function conflictsAsDiagnostics(conflicts) {
    return conflicts.map((conflict) => ({
        code: conflict.code ?? 'reimport-conflict',
        level: 'error',
        message: conflict.message ?? 'Reimport conflict',
        nodeId: conflict.nodeId ?? null,
    }));
}

function success(status, message, data, diagnostics = []) {
    return { data, diagnostics, message, ok: true, schema: BACKEND_SCHEMA, status };
}

function failure(status, message, data, diagnostics = [], exitCode = 1) {
    process.exitCode = exitCode;
    return { data, diagnostics, message, ok: false, schema: BACKEND_SCHEMA, status };
}

function fail(status, message, options = {}) {
    const error = new Error(message);
    error.status = status;
    error.exitCode = options.exitCode ?? 1;
    error.diagnostics = options.diagnostics ?? [];
    throw error;
}
