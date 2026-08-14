import { readFile } from 'node:fs/promises';
import { relative, resolve } from 'node:path';
import {
    applyAuthoredOverlay,
    parseAuthoredOverlay,
    parseDesignIR,
    reimportDesign,
    summarizeDesignIR,
} from '../src/index.js';
import {
    assertBytesUnchanged,
    assertImmutableSnapshot,
    assertSafeOutputPath,
    compareGeneratedFiles,
    generatedChange,
    loadOrCreateOverlay,
    projectPaths,
    readOptionalJson,
    readRequiredJson,
    relativeFiles,
    staleGeneratedAssets,
    validateImportGraph,
    writeTransaction,
} from './backend-filesystem.js';
import {
    generatedFiles,
    snapshotProvenance,
} from './backend-materialization.js';
import {
    conflictsAsDiagnostics,
    fail,
    failure,
    IMPORT_LOCK_SCHEMA,
    jsonBytes,
    requestedSourceKey,
    requiredSource,
    sourceArchiveArguments,
    success,
} from './backend-protocol.js';
import { loadSource, planAssets } from './backend-source.js';
import {
    acquireImportOperationLock,
    releaseImportOperationLock,
} from './import-operation-lock.js';

export async function executeBackendCommand(command, project, args) {
    if (command === 'import' || command === 'reimport') {
        return withImportLock(project, () => (
            command === 'import'
                ? importDesign(project, args)
                : reimportDesignFilesystem(project, args)
        ));
    }
    if (command === 'design-check' || command === 'design-diff') {
        return withImportLock(project, () => inspectGeneratedDesign(
            project,
            args,
            { failOnDrift: command === 'design-check' },
        ));
    }
    if (['build', 'run', 'test', 'capture', 'package'].includes(command)) {
        fail(
            'capability_unavailable',
            `The installed backend does not yet provide the '${command}' capability`,
            { exitCode: 4 },
        );
    }
    fail('unsupported_command', `vellum-backend does not implement '${command ?? ''}'`);
}

async function withImportLock(project, operation) {
    const lease = await acquireImportOperationLock(project, assertSafeOutputPath);
    try {
        return await operation();
    } finally {
        await releaseImportOperationLock(project, lease, assertSafeOutputPath);
    }
}

async function inspectGeneratedDesign(project, args, { failOnDrift }) {
    const sourceKey = requestedSourceKey(args);
    const pendingPaths = projectPaths(project, sourceKey, 'pending');
    const importLock = await readRequiredJson(pendingPaths.importLock, 'design import lock');
    if (importLock.schema !== IMPORT_LOCK_SCHEMA) {
        fail('invalid_import_lock', `Unsupported import lock schema '${importLock.schema ?? ''}'`);
    }
    const lockedSource = importLock.sources?.[sourceKey];
    if (!lockedSource) fail('source_not_imported', `Source '${sourceKey}' has not been imported`);

    const paths = projectPaths(project, sourceKey, lockedSource.activeRevision);
    const importGraph = await readRequiredJson(paths.importGraph, 'design import graph');
    validateImportGraph(importGraph, sourceKey, lockedSource);
    const overlay = parseAuthoredOverlay(await readFile(paths.overlay, 'utf8'));
    const provenance = await readRequiredJson(paths.snapshotProvenance, 'source snapshot provenance');
    validateSnapshotProvenance(provenance, sourceKey, lockedSource);

    const archive = provenance.sourceArtifact?.kind === 'pulp-zip'
        ? {
            member: provenance.sourceArtifact.member,
            name: provenance.sourceArtifact.name,
            path: paths.snapshotArchive,
            sha256: provenance.sourceArtifact.sha256,
        }
        : null;
    const loaded = await loadSource(paths.snapshotSource, {
        captureEnvelope: lockedSource.sourceArtifactKind === 'html' || lockedSource.sourceArtifactKind === 'claude-design-html'
            ? paths.snapshotCapture
            : undefined,
        sourceArchive: archive,
        sourceKey,
        sourceType: lockedSource.adapter,
    });
    validateRegeneratedSource(loaded, lockedSource, provenance);
    const assets = await planAssets(paths.snapshotSource, loaded.document, paths);
    const applied = applyAuthoredOverlay(loaded.document, overlay);
    if (applied.conflicts.length > 0) {
        fail('authored_overlay_conflict', 'The authored overlay no longer resolves against the active design', {
            diagnostics: conflictsAsDiagnostics(applied.conflicts),
        });
    }

    const expected = generatedFiles({
        applied,
        assets,
        captureEnvelope: loaded.captureBytes,
        document: loaded.document,
        overlayCreated: false,
        overlayValue: overlay,
        paths,
        project,
        sourceHash: loaded.sourceHash,
        sourceKey,
        sourceArtifact: provenance.sourceArtifact,
        sourceName: provenance.sourceName,
        sourceType: lockedSource.adapter,
    });
    // Import/reimport reports are historical evidence, not active materialized
    // output. The authored overlay and developer-extended import mount graph
    // are intentionally excluded as well; both are validated above.
    expected.delete(paths.importReport);
    expected.delete(paths.overlay);
    expected.delete(paths.importGraph);
    for (const asset of assets.copies) expected.set(asset.generatedPath, asset.bytes);

    const changes = await compareGeneratedFiles(project, expected);
    const expectedAssets = new Set(assets.copies.map((asset) => resolve(asset.generatedPath)));
    for (const path of await staleGeneratedAssets(paths.generatedAssets, assets.copies)) {
        if (!expectedAssets.has(resolve(path))) {
            changes.push(await generatedChange(project, path, null, 'unexpected'));
        }
    }
    changes.sort((left, right) => left.path.localeCompare(right.path));

    const data = {
        activeRevision: lockedSource.activeRevision,
        changes,
        checkedFiles: expected.size,
        sourceKey,
        summary: summarizeDesignIR(loaded.document),
    };
    if (changes.length === 0) {
        return success(
            'design_clean',
            `Generated design for '${sourceKey}' matches deterministic regeneration`,
            data,
            [...loaded.backendDiagnostics, ...assets.diagnostics],
        );
    }
    const diagnostics = changes.map((change) => ({
        code: `generated-${change.kind}`,
        level: failOnDrift ? 'error' : 'info',
        message: `Generated design file is ${change.kind}: ${change.path}`,
        path: change.path,
    }));
    if (failOnDrift) {
        return failure(
            'design_drift',
            `Generated design for '${sourceKey}' differs from deterministic regeneration`,
            data,
            diagnostics,
            2,
        );
    }
    return success(
        'design_diff',
        `Generated design for '${sourceKey}' has ${changes.length} deterministic difference(s)`,
        data,
        diagnostics,
    );
}

function validateSnapshotProvenance(provenance, sourceKey, lockedSource) {
    if (
        provenance?.schema !== 'vellum.source-snapshot.v1' ||
        provenance.sourceKey !== sourceKey ||
        provenance.revision !== lockedSource.activeRevision ||
        provenance.sourceHash !== lockedSource.snapshotHash ||
        provenance.sourceType !== lockedSource.adapter ||
        !provenance.sourceArtifact ||
        provenance.sourceArtifact.kind !== (lockedSource.sourceArtifactKind ?? 'json')
    ) {
        fail(
            'invalid_snapshot_provenance',
            `Active snapshot provenance no longer matches source '${sourceKey}'`,
        );
    }
}

function validateRegeneratedSource(loaded, lockedSource, provenance) {
    const document = loaded.document;
    if (
        loaded.revision !== lockedSource.activeRevision ||
        loaded.sourceHash !== lockedSource.snapshotHash ||
        document.source.adapterVersion !== lockedSource.adapterVersion ||
        document.source.formatVersion !== lockedSource.formatVersion ||
        document.source.namespace !== lockedSource.namespace ||
        loaded.sourceArtifact.kind !== provenance.sourceArtifact.kind ||
        loaded.sourceArtifact.sha256 !== provenance.sourceArtifact.sha256
    ) {
        fail(
            'snapshot_identity_mismatch',
            'Regenerated source identity differs from the active import lock',
        );
    }
}

async function importDesign(project, args) {
    const sourcePath = requiredSource(args);
    const sourceKey = requestedSourceKey(args);
    const sourceType = args['source-type'] ?? 'figma';
    const loaded = await loadSource(sourcePath, {
        captureEnvelope: args['capture-envelope'],
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
        captureEnvelope: loaded.captureBytes,
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
    if (loaded.captureBytes !== undefined) {
        await assertImmutableSnapshot(paths.snapshotCapture, loaded.captureBytes);
    }
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
    const sourceKey = requestedSourceKey(args);
    const pathsForLock = projectPaths(project, sourceKey, 'pending');
    const importLock = await readRequiredJson(pathsForLock.importLock, 'design import lock');
    if (importLock.schema !== IMPORT_LOCK_SCHEMA) {
        fail('invalid_import_lock', `Unsupported import lock schema '${importLock.schema ?? ''}'`);
    }
    const lockedSource = importLock.sources?.[sourceKey];
    if (!lockedSource) fail('source_not_imported', `Source '${sourceKey}' has not been imported`);
    const loaded = await loadSource(sourcePath, {
        captureEnvelope: args['capture-envelope'],
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
        const assets = await planAssets(sourcePath, loaded.document, paths);
        await assertImmutableSnapshot(paths.snapshotSource, loaded.sourceBytes);
        if (loaded.captureBytes !== undefined) {
            await assertImmutableSnapshot(paths.snapshotCapture, loaded.captureBytes);
        }
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
        const generated = generatedFiles({
            applied,
            assets,
            captureEnvelope: loaded.captureBytes,
            document: previous,
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
        const refreshed = new Map();
        for (const path of [
            paths.materializedUi,
            paths.generatedBindings,
            paths.resolvedTokenLayers,
        ]) {
            const expected = generated.get(path);
            const current = await readFile(path).catch((error) => {
                if (error.code === 'ENOENT') return null;
                throw error;
            });
            if (current === null || !current.equals(expected)) refreshed.set(path, expected);
        }
        if (refreshed.size > 0) {
            await writeTransaction(project, refreshed, {
                preconditions: new Map([
                    [paths.overlay, overlayBytesBefore],
                    [paths.importGraph, importGraphBytesBefore],
                ]),
            });
        }
        await assertBytesUnchanged(paths.overlay, overlayBytesBefore);
        await assertBytesUnchanged(paths.importGraph, importGraphBytesBefore);
        const status = refreshed.size > 0 ? 'reimport_rematerialized' : 'reimport_unchanged';
        const message = refreshed.size > 0
            ? `Rematerialized derived output for unchanged source '${sourceKey}' at revision '${loaded.revision}'`
            : `Source '${sourceKey}' is already at revision '${loaded.revision}'`;
        return success(status, message, {
            activeRevision: loaded.revision,
            files: relativeFiles(project, refreshed),
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
    if (loaded.captureBytes !== undefined) reviewFiles.set(paths.snapshotCapture, loaded.captureBytes);
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
        await writeTransaction(project, reviewFiles, {
            preconditions: new Map([
                [paths.overlay, overlayBytesBefore],
                [paths.importGraph, importGraphBytesBefore],
            ]),
        });
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
        captureEnvelope: loaded.captureBytes,
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
        preconditions: new Map([
            [paths.overlay, overlayBytesBefore],
            [paths.importGraph, importGraphBytesBefore],
        ]),
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
