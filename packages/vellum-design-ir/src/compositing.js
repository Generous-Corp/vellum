import { BLEND_MODES, MASK_MODES } from './constants.js';

export const COMPOSITING_KEYS = Object.freeze(['blendMode', 'isolate', 'mask', 'maskRef', 'opacity']);

/**
 * Checks a node's `compositing` object and returns its problems as
 * `{ code, message, path }` records. The object mirrors the renderer's scene
 * node: `opacity` in [0, 1], a `blendMode`, `isolate`, a `mask` mode that
 * makes the node a mask for its siblings, and `maskRef`, the identity of the
 * sibling mask node that applies to this node.
 */
export function compositingProblems(value, path) {
    const problems = [];
    const problem = (field, code, message) =>
        problems.push({ code, message, path: `${path}.${field}` });
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
        return [{ code: 'type', message: `${path} must be an object`, path }];
    }
    for (const key of Object.keys(value)) {
        if (!COMPOSITING_KEYS.includes(key)) problem(key, 'unknown-field', `unknown compositing field '${key}'`);
    }
    if (value.opacity !== undefined &&
        (typeof value.opacity !== 'number' || !Number.isFinite(value.opacity) ||
            value.opacity < 0 || value.opacity > 1)) {
        problem('opacity', 'range', 'opacity must be a number from 0 to 1');
    }
    if (value.blendMode !== undefined && !BLEND_MODES.includes(value.blendMode)) {
        problem('blendMode', 'enum', 'blendMode must be a known blend mode');
    }
    if (value.isolate !== undefined && typeof value.isolate !== 'boolean') {
        problem('isolate', 'type', 'isolate must be a boolean');
    }
    if (value.mask !== undefined && !MASK_MODES.includes(value.mask)) {
        problem('mask', 'enum', 'mask must be alpha or luminance');
    }
    if (value.maskRef !== undefined && (typeof value.maskRef !== 'string' || !value.maskRef)) {
        problem('maskRef', 'type', 'maskRef must be a non-empty string');
    }
    return problems;
}

/**
 * The canonical form of a valid `compositing` object: only the fields that
 * differ from their defaults (opacity 1, blend mode `normal`, no isolation),
 * so equal compositing always serializes equally. Absent when nothing is left.
 */
export function canonicalCompositing(value) {
    const output = {};
    if (value.blendMode !== undefined && value.blendMode !== 'normal') output.blendMode = value.blendMode;
    if (value.isolate === true) output.isolate = true;
    if (value.mask !== undefined) output.mask = value.mask;
    if (value.maskRef !== undefined) output.maskRef = value.maskRef;
    if (value.opacity !== undefined && value.opacity !== 1) output.opacity = value.opacity;
    return Object.keys(output).length > 0 ? output : undefined;
}
