import {
    COMPILER_NAME,
    COMPILER_VERSION,
    DESIGN_IR_SCHEMA,
    DESIGN_IR_VERSION,
} from './constants.js';
import { buildLossReport, diagnosticSort, normalizeDiagnostic } from './diagnostics.js';
import {
    assertSourceKey,
    assignIdentity,
    indexTree,
    signatureKey,
} from './identity.js';
import { canonicalize, deepClone } from './stable-json.js';
import { normalizeTokens } from './tokens.js';
import { validateDesignIR } from './validate.js';

const KNOWN_NODE_FIELDS = new Set([
    'children',
    'extensions',
    'kind',
    'losses',
    'name',
    'properties',
    'role',
    'semanticId',
    'sourceId',
    'text',
]);

/**
 * Normalize an adapter-produced source model into canonical DesignIR v1.
 * The function is pure and never reads a clock, filesystem, or network.
 */
export function normalizeImport(input, options = {}) {
    if (!input || typeof input !== 'object' || Array.isArray(input)) {
        throw new TypeError('import input must be an object');
    }
    const source = normalizeSource(input.source);
    const diagnostics = (input.diagnostics ?? []).map((diagnostic) =>
        normalizeDiagnostic(diagnostic),
    );
    const seenIds = new Set();

    const root = normalizeNode(input.root, {
        depth: 0,
        diagnostics,
        path: '$.root',
        seenIds,
        sourceKey: source.key,
    });

    const assets = normalizeAssets(input.assets ?? []);
    const document = canonicalize({
        $schema: DESIGN_IR_SCHEMA,
        assets,
        compiler: {
            name: COMPILER_NAME,
            version: options.compilerVersion ?? COMPILER_VERSION,
        },
        diagnostics: diagnostics.sort(diagnosticSort),
        root,
        schemaVersion: DESIGN_IR_VERSION,
        source,
        tokens: normalizeTokens(input.tokens, source.key),
    });
    document.lossReport = buildLossReport(document.diagnostics);
    validateDesignIR(document, { throwOnError: true });
    return canonicalize(document);
}

function normalizeSource(source) {
    if (!source || typeof source !== 'object' || Array.isArray(source)) {
        throw new TypeError('source must be an object');
    }
    const key = assertSourceKey(source.key);
    const adapter = requiredString(source.adapter, 'source.adapter');
    const adapterVersion = requiredString(source.adapterVersion, 'source.adapterVersion');
    const formatVersion = requiredString(source.formatVersion, 'source.formatVersion');
    const revision = requiredString(source.revision, 'source.revision');
    const namespace = source.namespace === undefined
        ? key
        : assertSourceKey(source.namespace, 'source.namespace');
    const output = { adapter, adapterVersion, formatVersion, key, namespace, revision };
    for (const field of ['snapshotHash', 'sourceUri', 'providerFileKey', 'providerNodeId']) {
        if (source[field] !== undefined) output[field] = String(source[field]);
    }
    if (source.provenance !== undefined) output.provenance = canonicalize(source.provenance);
    return output;
}

function normalizeNode(rawNode, context) {
    if (!rawNode || typeof rawNode !== 'object' || Array.isArray(rawNode)) {
        throw new TypeError(`${context.path} must be an object`);
    }
    const children = Array.isArray(rawNode.children) ? rawNode.children : [];
    const signatureCounts = new Map();
    const { id, identity } = assignIdentity(rawNode, {
        depth: context.depth,
        parentId: context.parentId ?? null,
        signatureOrdinal: context.signatureOrdinal ?? 0,
        sourceKey: context.sourceKey,
    });
    if (context.seenIds.has(id)) {
        throw new TypeError(`duplicate stable identity '${id}' at ${context.path}`);
    }
    context.seenIds.add(id);

    const output = {
        children: [],
        id,
        identity,
        kind: requiredString(rawNode.kind ?? 'view', `${context.path}.kind`).toLowerCase(),
        properties: canonicalize(rawNode.properties ?? {}),
    };
    for (const field of ['name', 'role', 'text']) {
        if (rawNode[field] !== undefined) output[field] = String(rawNode[field]);
    }
    if (rawNode.extensions !== undefined) output.extensions = canonicalize(rawNode.extensions);

    const unrecognized = Object.create(null);
    for (const key of Object.keys(rawNode)) {
        if (!KNOWN_NODE_FIELDS.has(key)) unrecognized[key] = deepClone(rawNode[key]);
    }
    if (Object.keys(unrecognized).length > 0) {
        output.extensions = {
            ...(output.extensions ?? {}),
            'dev.vellum.import.unrecognized.v1': canonicalize(unrecognized),
        };
        context.diagnostics.push({
            code: 'source-node-fields-preserved',
            detail: { fields: Object.keys(unrecognized).sort() },
            disposition: 'extension',
            message: 'Unrecognized source-node fields were preserved in an extension namespace',
            path: context.path,
            severity: 'info',
        });
    }

    for (const loss of rawNode.losses ?? []) {
        context.diagnostics.push(normalizeDiagnostic(loss, context.path));
    }

    for (let childIndex = 0; childIndex < children.length; childIndex += 1) {
        const child = children[childIndex];
        const key = signatureKey(child);
        const ordinal = signatureCounts.get(key) ?? 0;
        signatureCounts.set(key, ordinal + 1);

        // assignIdentity needs the sibling discriminator. Normalize the child
        // through an internal copy carrying that deterministic context value.
        output.children.push(normalizeNode(child, {
            ...context,
            depth: context.depth + 1,
            parentId: output.id,
            path: `${context.path}.children[${childIndex}]`,
            signatureOrdinal: ordinal,
        }));
    }
    return output;
}

function normalizeAssets(assets) {
    if (!Array.isArray(assets)) throw new TypeError('assets must be an array');
    const seen = new Set();
    const output = assets.map((asset, index) => {
        if (!asset || typeof asset !== 'object' || Array.isArray(asset)) {
            throw new TypeError(`assets[${index}] must be an object`);
        }
        const id = requiredString(asset.id, `assets[${index}].id`);
        if (seen.has(id)) throw new TypeError(`duplicate asset id '${id}'`);
        seen.add(id);
        const value = { id };
        for (const field of ['contentHash', 'mimeType', 'uri']) {
            if (asset[field] !== undefined) value[field] = String(asset[field]);
        }
        for (const field of ['height', 'width']) {
            if (asset[field] !== undefined) {
                const dimension = Number(asset[field]);
                if (!Number.isFinite(dimension) || dimension < 0) {
                    throw new TypeError(`assets[${index}].${field} must be a non-negative number`);
                }
                value[field] = dimension;
            }
        }
        if (asset.provenance !== undefined) value.provenance = canonicalize(asset.provenance);
        return canonicalize(value);
    });
    return output.sort((left, right) => left.id.localeCompare(right.id));
}

function requiredString(value, field) {
    if (typeof value !== 'string' || !value.trim()) {
        throw new TypeError(`${field} must be a non-empty string`);
    }
    return value.trim();
}

export function assertNoDuplicateIdentities(document) {
    const { duplicateIds } = indexTree(document.root);
    if (duplicateIds.length > 0) {
        throw new TypeError(`duplicate stable identities: ${[...new Set(duplicateIds)].join(', ')}`);
    }
}
