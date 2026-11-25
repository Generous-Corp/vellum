import { lstat, readFile } from 'node:fs/promises';
import { basename, dirname, join, relative, resolve, sep } from 'node:path';
import {
    decodeFigmaPluginExport,
    FIGMA_PLUGIN_FORMAT,
    lowerBrowserCaptureToDesignIR,
    normalizeImport,
    normalizeSha256ContentHash,
    parseDesignIR,
} from '../src/index.js';
import { decodeFigSource, outlineFigFile } from '../src/fig-file/import.js';
import { decodeNativeSource, nativeSourceTypes } from './backend-native-source.js';
import {
    fail,
    isSafeRelativeAsset,
    jsonBytes,
    sha256,
    validateRevision,
} from './backend-protocol.js';

const pluginSourceTypes = new Set(['figma', 'figma-plugin']);
const MAX_SOURCE_BYTES = 16 * 1024 * 1024;
const MAX_ARCHIVE_BYTES = 256 * 1024 * 1024;
const MAX_ASSET_BYTES = 128 * 1024 * 1024;
const MAX_ASSET_COUNT = 10_000;
const MAX_NODE_COUNT = 100_000;
const MAX_TREE_DEPTH = 512;

function isFigSource(file, sourceType) {
    return sourceType === 'fig' || (sourceType === 'figma' && file.toLowerCase().endsWith('.fig'));
}

async function readImportSource(file, byteLimit) {
    const metadata = await lstat(file).catch(() => null);
    if (!metadata?.isFile() || metadata.isSymbolicLink()) {
        fail('invalid_source', `Import source must be a regular file: ${file}`);
    }
    if (metadata.size > byteLimit) {
        fail('source_too_large', `Import source exceeds ${byteLimit} bytes`);
    }
    return readFile(file);
}

export async function outlineSource(sourcePath, sourceType) {
    const file = resolve(sourcePath);
    if (!isFigSource(file, sourceType)) {
        fail('invalid_fig_outline', '--outline requires a local Figma .fig source');
    }
    const bytes = await readImportSource(file, MAX_ARCHIVE_BYTES);
    try {
        return outlineFigFile(bytes);
    } catch (error) {
        fail('invalid_fig', `Figma .fig outline could not be decoded: ${error.message}`);
    }
}

export async function loadSource(sourcePath, {
    captureEnvelope, extraFiles = [], figFrame, figPage, sourceArchive, sourceKey, sourceType,
}) {
    const isPluginType = pluginSourceTypes.has(sourceType);
    if (!['fig', 'design-ir', 'html', 'claude-design'].includes(sourceType) &&
        !isPluginType && !nativeSourceTypes.has(sourceType)) {
        fail('unsupported_source_type', `Unsupported source type '${sourceType}'`);
    }
    const file = resolve(sourcePath);
    const isFig = isFigSource(file, sourceType);
    if (figFrame !== undefined && (!(isFig || isPluginType) || !figFrame.trim())) {
        fail('invalid_fig_frame', '--frame requires a Figma source and a frame GUID or name');
    }
    if (extraFiles.length > 0 && (!isPluginType || isFig || sourceArchive !== null || figFrame !== undefined)) {
        fail('invalid_arguments', 'Repeated files require Figma plugin JSON envelopes with one root each');
    }
    if (figPage !== undefined && (!isFig || figFrame === undefined || !figPage.trim())) {
        fail('invalid_fig_frame', '--page requires a local Figma .fig frame selector and a page name');
    }
    let sourceBytes = await readImportSource(file, isFig ? MAX_ARCHIVE_BYTES : MAX_SOURCE_BYTES);
    if (sourceArchive === null && !isFig && (
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
    if (isFig) {
        let decoded;
        try {
            decoded = decodeFigSource(sourceBytes, { sourceKey, frame: figFrame, page: figPage });
        } catch (error) {
            fail('invalid_fig', `Figma .fig source could not be decoded: ${error.message}`);
        }
        const sourceHash = `sha256:${sha256(sourceBytes)}`;
        const { frame: resolvedFrame, ...decodedParts } = decoded;
        const revision = validateRevision(decodedParts.document.source.revision);
        return {
            archiveBytes: null, backendDiagnostics: [], ...decodedParts, revision,
            // Persist the resolved GUID even when --frame was omitted, so reimport
            // cannot drift to a different "first frame" after the file is edited.
            sourceArtifact: {
                kind: 'fig', name: basename(file), sha256: sourceHash,
                frame: resolvedFrame,
            },
            sourceBytes, sourceHash, sourceName: basename(file),
        };
    }
    if (nativeSourceTypes.has(sourceType)) {
        const sourceHash = `sha256:${sha256(sourceBytes)}`;
        const decoded = decodeNativeSource(sourceBytes, { sourceKey, sourceHash, sourceType });
        return {
            ...decoded,
            archiveBytes: null,
            revision: validateRevision(decoded.document.source.revision),
            sourceArtifact: { kind: sourceType, name: basename(file), sha256: sourceHash },
            sourceBytes, sourceHash, sourceName: basename(file),
        };
    }
    let input = parseSourceJson(sourceBytes);
    const isPlugin = isPluginType && input?.format_version === FIGMA_PLUGIN_FORMAT;
    if (archive && (!isPlugin || input.$schema !== 'https://pulp.dev/schemas/figma-plugin-export-v1.json')) {
        fail('invalid_source_archive', 'Pulp ZIP scene must use the pinned Figma plugin export contract');
    }
    if ((sourceType === 'figma-plugin' || extraFiles.length > 0) && !isPlugin) {
        fail('invalid_figma_plugin', 'Import requires a pinned Figma plugin export');
    }
    if (figFrame !== undefined && !isPlugin) {
        fail('invalid_fig_frame', '--frame requires a local Figma .fig file or plugin envelope');
    }

    let embeddedAssets;
    if (extraFiles.length > 0) {
        const merged = await mergePluginSources(file, input, sourceBytes.length, extraFiles, sourceKey);
        ({ input, embeddedAssets } = merged);
        sourceBytes = jsonBytes(input);
        if (sourceBytes.length > MAX_SOURCE_BYTES) {
            fail('source_too_large', `Merged Figma source exceeds ${MAX_SOURCE_BYTES} bytes`);
        }
    }
    const sceneHash = `sha256:${sha256(sourceBytes)}`;
    const sourceHash = archive?.sha256 ?? sceneHash;
    enforceSourceLimits(input);
    const hasPluginStates = Array.isArray(input?.root?.alternate_frames);
    let resolvedFrame;
    if (figFrame !== undefined) {
        const frame = selectPluginFrame(input.root, figFrame);
        resolvedFrame = frame.figma_node_id;
        input = { ...input, root: frame };
    }
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
        if (isPlugin) {
            input = decodePlugin(input, { sourceHash, sourceKey });
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
    const frameMetadata = resolvedFrame === undefined ? {} : { frame: resolvedFrame };
    const sourceArtifact = archive ? {
        ...frameMetadata,
        kind: 'pulp-zip',
        member: archive.member,
        name: archive.name,
        sceneSha256: sceneHash,
        sha256: archive.sha256,
    } : {
        ...frameMetadata,
        kind: isPlugin && hasPluginStates ? 'figma-plugin-states' : 'json',
        name: basename(file),
        sha256: sceneHash,
    };
    return {
        archiveBytes: archive?.bytes ?? null,
        embeddedAssets,
        backendDiagnostics: [],
        document,
        revision,
        sourceArtifact,
        sourceBytes,
        sourceHash,
        sourceName: sourceArtifact.name,
    };
}

function parseSourceJson(bytes) {
    try {
        return JSON.parse(bytes.toString('utf8'));
    } catch (error) {
        fail('invalid_source_json', `Import source is not valid JSON: ${error.message}`);
    }
}

function decodePlugin(input, options) {
    try {
        return decodeFigmaPluginExport(input, options);
    } catch (error) {
        fail('invalid_figma_plugin', error.message);
    }
}

function selectPluginFrame(root, selector) {
    const frames = [];
    const name = selector.toLowerCase();
    const pending = [root];
    while (pending.length > 0) {
        const node = pending.pop();
        if (typeof node?.type === 'string' && node.type.toLowerCase() === 'frame' &&
            node.name !== '<multi-export>') frames.push(node);
        for (const children of [node?.children, node?.alternate_frames]) {
            if (Array.isArray(children)) {
                for (const child of children) pending.push(child);
            }
        }
    }
    const byId = frames.filter((frame) => frame.figma_node_id === selector);
    const matches = byId.length > 0 ? byId : frames.filter((frame) =>
        typeof frame.name === 'string' && frame.name.toLowerCase() === name);
    if (matches.length !== 1 || typeof matches[0].figma_node_id !== 'string') {
        fail('invalid_fig_frame', `Figma frame '${selector}' must resolve to exactly one frame`);
    }
    return matches[0];
}

async function mergePluginSources(primary, input, totalBytes, extraFiles, sourceKey) {
    const frames = [];
    const embeddedAssets = new Map();
    const assets = new Map();
    const fonts = new Map();
    const diagnostics = [];
    let assetBytes = 0;

    for (const [index, file] of [primary, ...extraFiles.map((name) => resolve(name))].entries()) {
        let envelope = input;
        if (index > 0) {
            const bytes = await readImportSource(file, MAX_SOURCE_BYTES - totalBytes);
            totalBytes += bytes.length;
            envelope = parseSourceJson(bytes);
        }
        enforceSourceLimits(envelope);
        if (envelope?.root?.alternate_frames !== undefined) {
            fail('invalid_figma_plugin', 'Repeated Figma inputs must each contain one exported frame');
        }
        const decoded = decodePlugin(envelope, { sourceHash: 'sha256:' + '0'.repeat(64), sourceKey });
        if (index > 0) frames.push(envelope.root);
        for (const diagnostic of envelope.diagnostics ?? []) diagnostics.push(diagnostic);
        for (const [assetIndex, asset] of decoded.assets.entries()) {
            const raw = envelope.asset_manifest.assets[assetIndex];
            const existing = assets.get(asset.id);
            if (existing && (existing.contentHash !== asset.contentHash || existing.uri !== asset.uri)) {
                fail('invalid_figma_plugin', `Figma state assets conflict for '${asset.id}'`);
            }
            assets.set(asset.id, existing ?? { ...asset, raw });
            if (asset.uri === undefined || !isSafeRelativeAsset(asset.uri)) continue;
            const bytes = await readSourceAsset(file, asset);
            if (bytes === undefined) continue;
            if (asset.contentHash !== undefined && asset.contentHash !== `sha256:${sha256(bytes)}`) {
                fail('asset_hash_mismatch', `Asset '${asset.id}' does not match its declared hash`);
            }
            const previous = embeddedAssets.get(asset.uri);
            if (previous && !previous.equals(bytes)) {
                fail('invalid_figma_plugin', `Figma state assets conflict at '${asset.uri}'`);
            }
            if (previous) continue;
            assetBytes += bytes.length;
            if (assetBytes > MAX_ARCHIVE_BYTES) {
                fail('asset_too_large', `Figma state assets exceed ${MAX_ARCHIVE_BYTES} bytes`);
            }
            embeddedAssets.set(asset.uri, bytes);
        }
        for (const font of envelope.font_family_assets ?? []) {
            const id = font?.asset_id ?? fonts.size;
            const existing = fonts.get(id);
            if (existing && existing.content_hash !== font.content_hash) {
                fail('invalid_figma_plugin', `Figma state fonts conflict for '${id}'`);
            }
            fonts.set(id, existing ?? font);
        }
    }
    return {
        input: {
            ...input,
            asset_manifest: { ...input.asset_manifest, version: 1, assets: [...assets.values()].map(({ raw }) => raw) },
            diagnostics,
            font_family_assets: [...fonts.values()],
            root: { ...input.root, alternate_frames: frames },
        },
        embeddedAssets,
    };
}

export async function planAssets(sourcePath, { document, embeddedAssets }, paths) {
    const manifest = [];
    const copies = [];
    const diagnostics = [];
    for (const asset of document.assets) {
        const receipt = { ...asset, status: 'declared' };
        if (typeof asset.uri === 'string' && isSafeRelativeAsset(asset.uri)) {
            const bytes = embeddedAssets
                ? embeddedAssets.get(asset.uri)
                : await readSourceAsset(sourcePath, asset);
            if (bytes !== undefined) {
                if (bytes.length > MAX_ASSET_BYTES) {
                    fail('asset_too_large', `Asset '${asset.id}' exceeds ${MAX_ASSET_BYTES} bytes`);
                }
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

async function readSourceAsset(sourcePath, asset) {
    const directory = dirname(resolve(sourcePath));
    const sourceAsset = resolve(directory, asset.uri);
    await assertSafeSourceAsset(directory, sourceAsset);
    const metadata = await lstat(sourceAsset).catch(() => null);
    if (!metadata?.isFile() || metadata.isSymbolicLink()) return undefined;
    if (metadata.size > MAX_ASSET_BYTES) {
        fail('asset_too_large', `Asset '${asset.id}' exceeds ${MAX_ASSET_BYTES} bytes`);
    }
    return readFile(sourceAsset);
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
    if (!pluginSourceTypes.has(sourceType)) {
        fail('invalid_source_archive', 'Pulp ZIP sources require a Figma plugin source type');
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
    const assets = input?.assets ?? input?.asset_manifest?.assets;
    if (Array.isArray(assets) && assets.length > MAX_ASSET_COUNT) {
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
        for (const children of [node?.children, node?.alternate_frames]) {
            if (Array.isArray(children)) {
                for (const child of children) stack.push({ depth: depth + 1, node: child });
            }
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
