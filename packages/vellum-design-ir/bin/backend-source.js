import { lstat, readFile } from 'node:fs/promises';
import { basename, dirname, join, relative, resolve, sep } from 'node:path';
import {
    decodeFigmaPluginExport,
    lowerBrowserCaptureToDesignIR,
    normalizeImport,
    normalizeSha256ContentHash,
    parseDesignIR,
} from '../src/index.js';
import {
    fail,
    isSafeRelativeAsset,
    sha256,
    validateRevision,
} from './backend-protocol.js';

const MAX_SOURCE_BYTES = 16 * 1024 * 1024;
const MAX_ARCHIVE_BYTES = 256 * 1024 * 1024;
const MAX_ASSET_BYTES = 128 * 1024 * 1024;
const MAX_ASSET_COUNT = 10_000;
const MAX_NODE_COUNT = 100_000;
const MAX_TREE_DEPTH = 512;

export async function loadSource(sourcePath, { captureEnvelope, sourceArchive, sourceKey, sourceType }) {
    if (!['figma', 'design-ir', 'html', 'claude-design'].includes(sourceType)) {
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
    if (sourceType === 'html' || sourceType === 'claude-design') {
        if (captureEnvelope === undefined) {
            fail('capture_required', 'HTML imports require a browser capture envelope');
        }
        const envelopeFile = resolve(captureEnvelope);
        const envelopeMetadata = await lstat(envelopeFile).catch(() => null);
        if (!envelopeMetadata?.isFile() || envelopeMetadata.isSymbolicLink()) {
            fail('invalid_capture', 'Browser capture envelope must be a regular file');
        }
        if (envelopeMetadata.size > MAX_SOURCE_BYTES) {
            fail('capture_too_large', `Browser capture envelope exceeds ${MAX_SOURCE_BYTES} bytes`);
        }
        let envelope;
        try {
            envelope = JSON.parse((await readFile(envelopeFile)).toString('utf8'));
        } catch (error) {
            fail('invalid_capture', `Browser capture envelope is not valid JSON: ${error.message}`);
        }
        const sourceHash = `sha256:${sha256(sourceBytes)}`;
        let document;
        try {
            document = lowerBrowserCaptureToDesignIR(envelope, {
                sourceKey,
                sourceType,
                snapshotHash: sourceHash,
                sourceUri: `file:${envelope.source?.entry ?? basename(file)}`,
            });
        } catch (error) {
            fail('invalid_capture', `Browser capture envelope is invalid: ${error.message}`);
        }
        const revision = validateRevision(document.source.revision);
        const captureSource = envelope.source ?? {};
        const sourceArtifact = {
            kind: sourceType === 'claude-design' ? 'claude-design-html' : 'html',
            name: envelope.source?.entry ?? basename(file),
            sha256: sourceHash,
            captureId: envelope.captureId,
            producer: captureSource.producer ?? null,
            fingerprint: captureSource.fingerprint ?? null,
            preflightSchema: captureSource.preflightSchema ?? null,
            dependencies: captureSource.dependencies ?? [],
        };
        return {
            archiveBytes: null,
            backendDiagnostics: [],
            captureBytes: await readFile(envelopeFile),
            document,
            revision,
            sourceArtifact,
            sourceBytes,
            sourceHash,
            sourceName: sourceArtifact.name,
        };
    }
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

export async function planAssets(sourcePath, document, paths) {
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
                const relativeAsset = asset.uri.split('/').join(sep);
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

function hasZipMagic(bytes) {
    if (bytes.length < 4 || bytes[0] !== 0x50 || bytes[1] !== 0x4b) return false;
    return (
        (bytes[2] === 0x03 && bytes[3] === 0x04) ||
        (bytes[2] === 0x05 && bytes[3] === 0x06) ||
        (bytes[2] === 0x07 && bytes[3] === 0x08)
    );
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
    if (!relativePath || relativePath.startsWith(`..${sep}`) || relativePath === '..') {
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
