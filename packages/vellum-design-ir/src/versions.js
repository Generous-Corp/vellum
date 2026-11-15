import {
    DESIGN_IR_COMPOSITING_VERSION,
    DESIGN_IR_VECTOR_ASSET_VERSION,
    DESIGN_IR_VECTOR_PAINT_KEYS,
    DESIGN_IR_VERSION,
    DESIGN_IR_VERSIONS,
} from './constants.js';

function isObject(value) {
    return value !== null && typeof value === 'object' && !Array.isArray(value);
}

// Why a node needs DesignIR version 6, or null: a path or vector node, a
// path or vector property bucket, an image source, or a vector paint key.
function vectorAssetField(node) {
    if (node.kind === 'path' || node.kind === 'vector') return `kind ${node.kind}`;
    const properties = isObject(node.properties) ? node.properties : {};
    if (properties.path !== undefined) return 'properties.path';
    if (properties.vector !== undefined) return 'properties.vector';
    if (isObject(properties.image) && properties.image.source !== undefined) {
        return 'properties.image.source';
    }
    if (isObject(properties.paint)) {
        for (const key of DESIGN_IR_VECTOR_PAINT_KEYS) {
            if (properties.paint[key] !== undefined) return `properties.paint.${key}`;
        }
    }
    return null;
}

/** The first node, depth first, that uses a version 6 field, as `{ path, field }`. */
export function findVectorAssetField(root, path = '$.root') {
    if (!isObject(root)) return null;
    const field = vectorAssetField(root);
    if (field) return { path, field };
    const children = Array.isArray(root.children) ? root.children : [];
    for (let index = 0; index < children.length; index += 1) {
        const found = findVectorAssetField(children[index], `${path}.children[${index}]`);
        if (found) return found;
    }
    return null;
}

function usesCompositing(node) {
    return isObject(node) && (node.compositing !== undefined ||
        (Array.isArray(node.children) && node.children.some(usesCompositing)));
}

/** The oldest DesignIR version that carries every field the tree uses. */
export function requiredDesignIRVersion(root) {
    if (findVectorAssetField(root)) return DESIGN_IR_VECTOR_ASSET_VERSION;
    return usesCompositing(root) ? DESIGN_IR_COMPOSITING_VERSION : DESIGN_IR_VERSION;
}

/**
 * Why a document's version cannot be read, or null. Every version from 1 to
 * 6 is read, and a field is legal only at or after the version that adds it.
 * The `compositing` object's own check lives with its validation.
 */
export function designIRVersionProblem(document) {
    const version = document.schemaVersion;
    if (!DESIGN_IR_VERSIONS.includes(version)) {
        return `$.schemaVersion must be one of ${DESIGN_IR_VERSIONS.join(', ')}`;
    }
    const found = findVectorAssetField(document.root);
    if (found && version < DESIGN_IR_VECTOR_ASSET_VERSION) {
        return `${found.path} uses ${found.field}, which needs schemaVersion ` +
            `${DESIGN_IR_VECTOR_ASSET_VERSION}`;
    }
    return null;
}
