import { createHash } from 'node:crypto';
import { ByteReader, makeDecoder, readSchema } from './kiwi.mjs';
import { unpackFig } from './container.mjs';
import { buildScene, findFrame, framesByName, materializeFrame, nodesByName, outline } from './scene.mjs';
import { decodeFigmaPluginExport } from '../figma-plugin.js';
import { normalizeImport } from '../normalize.js';

export const FIG_FILE_PARSER_VERSION = '0.1.0';
export const FIG_FILE_COMPAT_SCHEMA_VERSION = '0.3';

/** Decode one local Figma .fig file into Vellum's canonical DesignIR. */
export function decodeFigFile(bytes, options) {
    return decodeFigSource(bytes, options).document;
}

function readFigScene(bytes) {
    const container = unpackFig(bytes);
    const schema = readSchema(new ByteReader(container.schemaBytes));
    const decoder = makeDecoder(schema);
    if (decoder.rootIndex === undefined) throw new TypeError('schema has no root Message type');
    const message = decoder.decodeMessage(new ByteReader(container.messageBytes), decoder.rootIndex);
    return { container, scene: buildScene(message) };
}

export function outlineFigFile(bytes) {
    const { container, scene } = readFigScene(bytes);
    return outline(scene, container.meta);
}

export function decodeFigSource(bytes, { sourceKey = 'main', frame, page } = {}) {
    const { container, scene } = readFigScene(bytes);
    const selected = selectFrame(scene, frame ?? firstFrame(scene), page);
    const { envelope } = materializeFrame(scene, selected, {
        images: container.images,
        solvedLayout: true,
        fileKey: null,
        parserVersion: FIG_FILE_PARSER_VERSION,
        compatSchemaVersion: FIG_FILE_COMPAT_SCHEMA_VERSION,
        exportedAt: '1970-01-01T00:00:00Z',
    });
    const sourceHash = `sha256:${sha256(bytes)}`;
    preparePluginNode(envelope.root);
    envelope.diagnostics = envelope.diagnostics.map(({ detail, ...diagnostic }) => ({
        ...diagnostic,
        kind: 'capture_partial',
        message: detail || diagnostic.code,
    }));
    const embeddedAssets = new Map();
    for (const asset of envelope.asset_manifest.assets) {
        const bytes = container.images.get(asset.hash);
        if (!bytes) continue;
        embeddedAssets.set(asset.local_path, bytes);
        asset.content_hash = `sha256:${sha256(bytes)}`;
    }
    const document = normalizeImport(decodeFigmaPluginExport(envelope, { sourceHash, sourceKey }));
    return { document, embeddedAssets, frame: guidKey(selected.guid) };
}

function selectFrame(scene, selector, page) {
    if (selector && !scene.byGuid.has(selector)) {
        const frames = framesByName(scene, selector, page);
        const candidates = frames.length === 0 && !page ? nodesByName(scene, selector) : frames;
        if (candidates.length > 1) {
            const choices = candidates.map(({ guid, page }) => page ? `${guid} (${page})` : guid);
            throw new TypeError(`frame name '${selector}' is ambiguous. Use a GUID: ${choices.join(', ')}`);
        }
    }
    const selected = findFrame(scene, selector, page);
    if (!selected) {
        const scope = page ? ` on page '${page}'` : '';
        throw new TypeError(`frame not found: ${selector ?? '(first frame)'}${scope}`);
    }
    return selected;
}

function firstFrame(scene) {
    const page = scene.pages[0];
    const children = page ? (scene.childrenOf.get(guidKey(page.guid)) ?? []) : [];
    const node = children.find((candidate) => candidate.type !== 'CANVAS');
    return node ? guidKey(node.guid) : null;
}

function guidKey(guid) {
    return guid ? `${guid.sessionID}:${guid.localID}` : null;
}

function sha256(bytes) {
    return createHash('sha256').update(bytes).digest('hex');
}

function preparePluginNode(node) {
    if (!node || typeof node !== 'object') return;
    if (node.node_id && !node.figma_node_id) node.figma_node_id = node.node_id;
    // The offline decoder attaches a compatibility caption for Pulp audio widgets.
    // Expanded text nodes already carry the visible caption.
    delete node.label;
    for (const child of node.children ?? []) preparePluginNode(child);
}
