import {
    AUTHORED_OVERLAY_SCHEMA,
    AUTHORED_OVERLAY_VERSION,
} from './constants.js';
import { indexTree } from './identity.js';
import { canonicalize, deepClone } from './stable-json.js';
import { resolveTokenLayers } from './tokens.js';
import { validateAuthoredOverlay, validateDesignIR } from './validate.js';

export function emptyAuthoredOverlay(sourceKey) {
    return {
        $schema: AUTHORED_OVERLAY_SCHEMA,
        aliases: {},
        bindings: [],
        overrides: [],
        schemaVersion: AUTHORED_OVERLAY_VERSION,
        semanticTokens: {},
        sourceKey,
        themeOverrides: {},
    };
}

/**
 * Apply developer-owned bindings and structured property overrides without
 * mutating either canonical generated DesignIR or the authored sidecar.
 */
export function applyAuthoredOverlay(document, overlay) {
    validateDesignIR(document, { throwOnError: true });
    validateAuthoredOverlay(overlay, { throwOnError: true });
    if (overlay.sourceKey !== document.source.key) {
        throw new TypeError(
            `overlay sourceKey '${overlay.sourceKey}' does not match '${document.source.key}'`,
        );
    }

    const materialized = deepClone(document);
    const { index } = indexTree(materialized.root);
    const conflicts = [];
    const aliasesApplied = [];
    const resolvedBindings = [];
    const appliedOverrides = [];

    for (let indexValue = 0; indexValue < overlay.bindings.length; indexValue += 1) {
        const binding = overlay.bindings[indexValue];
        const resolved = resolveReference(binding.nodeId, index, overlay.aliases);
        if (!resolved.ok) {
            conflicts.push(referenceConflict('binding', indexValue, binding.nodeId, resolved));
            continue;
        }
        if (resolved.aliasPath.length > 0) {
            aliasesApplied.push(aliasReceipt('binding', indexValue, binding.nodeId, resolved));
        }
        resolvedBindings.push(canonicalize({
            ...deepClone(binding),
            originalNodeId: binding.nodeId,
            resolvedNodeId: resolved.nodeId,
        }));
    }

    for (let indexValue = 0; indexValue < overlay.overrides.length; indexValue += 1) {
        const override = overlay.overrides[indexValue];
        const resolved = resolveReference(override.nodeId, index, overlay.aliases);
        if (!resolved.ok) {
            conflicts.push(referenceConflict('override', indexValue, override.nodeId, resolved));
            continue;
        }
        if (resolved.aliasPath.length > 0) {
            aliasesApplied.push(aliasReceipt('override', indexValue, override.nodeId, resolved));
        }
        const entry = index.get(resolved.nodeId);
        setOwnedProperty(entry.node, override.path, deepClone(override.value));
        appliedOverrides.push(canonicalize({
            nodeId: resolved.nodeId,
            originalNodeId: override.nodeId,
            path: override.path,
        }));
    }

    const tokenLayers = resolveTokenLayers(materialized, overlay);
    for (const diagnostic of tokenLayers.diagnostics) {
        conflicts.push({
            code: diagnostic.code,
            kind: 'token',
            message: diagnostic.message,
            path: diagnostic.path,
        });
    }

    return {
        aliasesApplied: aliasesApplied.sort(receiptSort),
        appliedOverrides: appliedOverrides.sort(receiptSort),
        conflicts: conflicts.sort(conflictSort),
        materialized: canonicalize(materialized),
        resolvedBindings: resolvedBindings.sort((left, right) =>
            `${left.resolvedNodeId}\u0000${left.event}\u0000${left.action}`.localeCompare(
                `${right.resolvedNodeId}\u0000${right.event}\u0000${right.action}`,
            ),
        ),
        tokenLayers,
    };
}

export function resolveReference(nodeId, nodeIndex, aliases) {
    if (nodeIndex.has(nodeId)) return { aliasPath: [], nodeId, ok: true };
    const visited = new Set([nodeId]);
    const aliasPath = [];
    let current = nodeId;
    while (Object.hasOwn(aliases, current)) {
        const next = aliases[current];
        aliasPath.push({ from: current, to: next });
        if (visited.has(next)) {
            return { aliasPath, ok: false, reason: 'alias-cycle' };
        }
        visited.add(next);
        current = next;
        if (nodeIndex.has(current)) return { aliasPath, nodeId: current, ok: true };
    }
    return { aliasPath, ok: false, reason: 'identity-removed' };
}

function setOwnedProperty(node, path, value) {
    const segments = path.split('.');
    if (
        segments[0] !== 'properties' ||
        segments.length < 2 ||
        segments.slice(1).some((segment) =>
            !/^[A-Za-z_][A-Za-z0-9_-]*$/.test(segment) ||
            ['__proto__', 'constructor', 'prototype'].includes(segment),
        )
    ) {
        throw new TypeError(`override path '${path}' crosses the generated/authored boundary`);
    }
    let target = node;
    for (let index = 0; index < segments.length - 1; index += 1) {
        const segment = segments[index];
        const current = Object.hasOwn(target, segment)
            ? Object.getOwnPropertyDescriptor(target, segment)?.value
            : undefined;
        if (!current || typeof current !== 'object' || Array.isArray(current)) {
            defineOwnedProperty(target, segment, {});
        }
        target = Object.getOwnPropertyDescriptor(target, segment).value;
    }
    const leaf = segments.at(-1);
    if (value === null) Reflect.deleteProperty(target, leaf);
    else defineOwnedProperty(target, leaf, value);
}

function defineOwnedProperty(target, key, value) {
    Object.defineProperty(target, key, {
        configurable: true,
        enumerable: true,
        value,
        writable: true,
    });
}

function referenceConflict(kind, index, nodeId, resolved) {
    return {
        aliasPath: resolved.aliasPath,
        code: resolved.reason,
        index,
        kind,
        message: `Authored ${kind} '${nodeId}' has no resolved node in the new design`,
        nodeId,
    };
}

function aliasReceipt(kind, index, nodeId, resolved) {
    return {
        aliasPath: resolved.aliasPath,
        index,
        kind,
        originalNodeId: nodeId,
        resolvedNodeId: resolved.nodeId,
    };
}

function receiptSort(left, right) {
    return `${left.kind}\u0000${left.index}`.localeCompare(`${right.kind}\u0000${right.index}`);
}

function conflictSort(left, right) {
    return `${left.kind}\u0000${left.nodeId ?? left.path ?? ''}\u0000${left.code}`.localeCompare(
        `${right.kind}\u0000${right.nodeId ?? right.path ?? ''}\u0000${right.code}`,
    );
}
