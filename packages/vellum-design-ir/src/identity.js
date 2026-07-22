import { fnv1a32, stableStringify } from './stable-json.js';

const SAFE_SEGMENT = /^[A-Za-z0-9._:-]+$/;

export function assertSourceKey(value, field = 'source.key') {
    if (typeof value !== 'string' || !/^[a-z][a-z0-9-]{0,62}$/.test(value)) {
        throw new TypeError(
            `${field} must start with a lowercase letter and contain only ` +
                'lowercase letters, digits, and hyphens',
        );
    }
    return value;
}

export function encodeIdentitySegment(value) {
    const stringValue = String(value).trim();
    if (!stringValue) throw new TypeError('identity segment must not be empty');
    if (SAFE_SEGMENT.test(stringValue) && stringValue !== '.' && stringValue !== '..') {
        return stringValue;
    }
    return encodeURIComponent(stringValue);
}

export function namespacedIdentity(sourceKey, localIdentity) {
    return `${assertSourceKey(sourceKey)}/${encodeIdentitySegment(localIdentity)}`;
}

export function normalizeText(value) {
    return typeof value === 'string'
        ? value.normalize('NFC').replace(/\s+/g, ' ').trim().toLowerCase()
        : '';
}

export function structuralSignature(node) {
    return {
        kind: node.kind ?? 'view',
        name: normalizeText(node.name),
        role: normalizeText(node.role),
        text: normalizeText(node.text),
    };
}

/**
 * Identity priority: provider ID, explicit semantic ID, deterministic
 * structural/content identity. Reviewed aliases are applied only at reimport,
 * so a heuristic never silently becomes canonical identity.
 */
export function assignIdentity(rawNode, context) {
    const { sourceKey, depth, parentId = null, signatureOrdinal } = context;
    if (typeof rawNode.sourceId === 'string' && rawNode.sourceId.trim()) {
        return {
            id: namespacedIdentity(sourceKey, rawNode.sourceId),
            identity: {
                strategy: 'provider',
                sourceId: rawNode.sourceId,
            },
        };
    }
    if (typeof rawNode.semanticId === 'string' && rawNode.semanticId.trim()) {
        return {
            id: namespacedIdentity(sourceKey, rawNode.semanticId),
            identity: {
                strategy: 'semantic',
                semanticId: rawNode.semanticId,
            },
        };
    }

    const signature = structuralSignature(rawNode);
    const hash = fnv1a32({ depth, parentId, signature, signatureOrdinal });
    return {
        id: namespacedIdentity(sourceKey, `generated-${hash}`),
        identity: {
            strategy: 'structural',
            fingerprint: hash,
            ordinal: signatureOrdinal,
        },
    };
}

export function indexTree(root) {
    const index = new Map();
    const duplicateIds = [];

    function visit(node, parentId = null, childIndex = 0, path = '$.root') {
        if (index.has(node.id)) duplicateIds.push(node.id);
        index.set(node.id, { node, parentId, childIndex, path });
        for (let indexValue = 0; indexValue < node.children.length; indexValue += 1) {
            visit(
                node.children[indexValue],
                node.id,
                indexValue,
                `${path}.children[${indexValue}]`,
            );
        }
    }
    visit(root);
    return { index, duplicateIds };
}

export function identitySetFingerprint(root) {
    return fnv1a32([...indexTree(root).index.keys()].sort());
}

export function signatureKey(node) {
    return stableStringify(structuralSignature(node), { space: 0, newline: false });
}
