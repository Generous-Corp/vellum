import {
    AUTHORED_OVERLAY_SCHEMA,
    AUTHORED_OVERLAY_VERSION,
    DESIGN_IR_COMPOSITING_VERSION,
    DESIGN_IR_SCHEMA,
    DESIGN_IR_MATERIALS_VERSION,
    DESIGN_IR_TRACKS_VERSION,
    DESIGN_IR_VERSIONS,
    DISPOSITIONS,
    REIMPORT_REPORT_SCHEMA,
    REIMPORT_REPORT_VERSION,
    SEVERITIES,
} from './constants.js';
import { compositingProblems } from './compositing.js';
import { DesignIRValidationError } from './errors.js';
import { indexTree } from './identity.js';
import { jsonEqual } from './stable-json.js';

export function validateDesignIR(value, options = {}) {
    const issues = [];
    if (!isObject(value)) {
        issues.push(issue('$', 'type', 'DesignIR must be an object'));
        return finish(issues, options, 'Invalid DesignIR');
    }
    exact(value.$schema, DESIGN_IR_SCHEMA, '$.$schema', issues);
    if (!DESIGN_IR_VERSIONS.includes(value.schemaVersion)) {
        issues.push(issue(
            '$.schemaVersion',
            'version',
            `$.schemaVersion must be one of ${DESIGN_IR_VERSIONS.join(', ')}`,
        ));
    }
    requireObject(value.source, '$.source', issues);
    if (isObject(value.source)) {
        string(value.source.key, '$.source.key', issues);
        string(value.source.namespace, '$.source.namespace', issues);
        string(value.source.adapter, '$.source.adapter', issues);
        string(value.source.adapterVersion, '$.source.adapterVersion', issues);
        string(value.source.formatVersion, '$.source.formatVersion', issues);
        string(value.source.revision, '$.source.revision', issues);
    }
    requireObject(value.compiler, '$.compiler', issues);
    if (isObject(value.compiler)) {
        exact(value.compiler.name, '@vellum/design-ir', '$.compiler.name', issues);
        string(value.compiler.version, '$.compiler.version', issues);
    }
    requireObject(value.tokens, '$.tokens', issues);
    if (isObject(value.tokens)) {
        for (const [name, token] of Object.entries(value.tokens)) {
            const path = `$.tokens.${name}`;
            if (value.source?.key && !name.startsWith(`${value.source.key}.`)) {
                issues.push(issue(path, 'namespace', `token must begin with '${value.source.key}.'`));
            }
            requireObject(token, path, issues);
            if (isObject(token)) {
                string(token.$type, `${path}.$type`, issues);
                if (!Object.hasOwn(token, '$value')) {
                    issues.push(issue(`${path}.$value`, 'required', 'token $value is required'));
                }
                string(token.sourcePath, `${path}.sourcePath`, issues);
            }
        }
    }
    if (!Array.isArray(value.assets)) {
        issues.push(issue('$.assets', 'type', 'assets must be an array'));
    } else {
        const assetIds = new Set();
        value.assets.forEach((asset, index) => {
            const path = `$.assets[${index}]`;
            requireObject(asset, path, issues);
            if (!isObject(asset)) return;
            string(asset.id, `${path}.id`, issues);
            if (
                asset.contentHash !== undefined &&
                (typeof asset.contentHash !== 'string' ||
                    !/^sha256:[0-9a-f]{64}$/.test(asset.contentHash))
            ) {
                issues.push(issue(
                    `${path}.contentHash`,
                    'format',
                    "contentHash must be canonical lowercase 'sha256:<64 hex>'",
                ));
            }
            if (assetIds.has(asset.id)) issues.push(issue(`${path}.id`, 'duplicate-id', 'asset id is duplicated'));
            assetIds.add(asset.id);
            for (const field of ['height', 'width']) {
                if (
                    asset[field] !== undefined &&
                    (typeof asset[field] !== 'number' || !Number.isFinite(asset[field]) || asset[field] < 0)
                ) {
                    issues.push(issue(`${path}.${field}`, 'range', `${field} must be a non-negative number`));
                }
            }
        });
    }
    if (!Array.isArray(value.diagnostics)) {
        issues.push(issue('$.diagnostics', 'type', 'diagnostics must be an array'));
    } else {
        value.diagnostics.forEach((diagnostic, index) =>
            validateDiagnostic(diagnostic, `$.diagnostics[${index}]`, issues),
        );
    }
    validateNode(value.root, '$.root', value.source?.key, issues, value.schemaVersion);
    if (isObject(value.root) && value.root.compositing?.mask !== undefined) {
        issues.push(issue('$.root.compositing.mask', 'mask', 'the root has no siblings to mask'));
    }
    if (isObject(value.root) && value.root.compositing?.maskRef !== undefined) {
        issues.push(issue('$.root.compositing.maskRef', 'mask', 'the root has no siblings to be masked by'));
    }
    requireObject(value.lossReport, '$.lossReport', issues);
    if (isObject(value.lossReport) && Array.isArray(value.diagnostics)) {
        const expectedLosses = value.diagnostics.filter((diagnostic) =>
            ['rasterized', 'web-only', 'unsupported'].includes(diagnostic.disposition),
        );
        if (value.lossReport.lossCount !== expectedLosses.length) {
            issues.push(issue('$.lossReport.lossCount', 'inconsistent', 'lossCount does not match diagnostics'));
        }
        if (value.lossReport.totalDiagnostics !== value.diagnostics.length) {
            issues.push(issue(
                '$.lossReport.totalDiagnostics',
                'inconsistent',
                'totalDiagnostics does not match diagnostics',
            ));
        }
        if (!Array.isArray(value.lossReport.losses) || !jsonEqual(value.lossReport.losses, expectedLosses)) {
            issues.push(issue('$.lossReport.losses', 'inconsistent', 'losses do not match diagnostics'));
        }
    }
    if (isObject(value.root)) {
        const { duplicateIds } = indexTree(value.root);
        for (const id of new Set(duplicateIds)) {
            issues.push(issue('$.root', 'duplicate-id', `stable identity '${id}' is duplicated`));
        }
    }
    return finish(issues, options, 'Invalid DesignIR');
}

export function validateAuthoredOverlay(value, options = {}) {
    const issues = [];
    if (!isObject(value)) {
        issues.push(issue('$', 'type', 'authored overlay must be an object'));
        return finish(issues, options, 'Invalid authored overlay');
    }
    exact(value.$schema, AUTHORED_OVERLAY_SCHEMA, '$.$schema', issues);
    exact(value.schemaVersion, AUTHORED_OVERLAY_VERSION, '$.schemaVersion', issues);
    string(value.sourceKey, '$.sourceKey', issues);
    const identityPrefix = typeof value.sourceKey === 'string' ? `${value.sourceKey}/` : null;
    requireObject(value.aliases, '$.aliases', issues);
    if (isObject(value.aliases)) {
        for (const [from, to] of Object.entries(value.aliases)) {
            string(from, '$.aliases key', issues);
            string(to, `$.aliases.${from}`, issues);
            validateIdentityNamespace(from, identityPrefix, '$.aliases key', issues);
            validateIdentityNamespace(to, identityPrefix, `$.aliases.${from}`, issues);
        }
    }
    if (!Array.isArray(value.bindings)) {
        issues.push(issue('$.bindings', 'type', 'bindings must be an array'));
    } else {
        value.bindings.forEach((binding, index) => {
            const path = `$.bindings[${index}]`;
            requireObject(binding, path, issues);
            if (isObject(binding)) {
                string(binding.nodeId, `${path}.nodeId`, issues);
                validateIdentityNamespace(binding.nodeId, identityPrefix, `${path}.nodeId`, issues);
                string(binding.event, `${path}.event`, issues);
                string(binding.action, `${path}.action`, issues);
            }
        });
    }
    if (!Array.isArray(value.overrides)) {
        issues.push(issue('$.overrides', 'type', 'overrides must be an array'));
    } else {
        value.overrides.forEach((override, index) => {
            const path = `$.overrides[${index}]`;
            requireObject(override, path, issues);
            if (isObject(override)) {
                string(override.nodeId, `${path}.nodeId`, issues);
                validateIdentityNamespace(override.nodeId, identityPrefix, `${path}.nodeId`, issues);
                string(override.path, `${path}.path`, issues);
                if (!isSafeOverridePath(override.path)) {
                    issues.push(issue(
                        `${path}.path`,
                        'ownership-boundary',
                        'override must address properties.<field> and may not contain unsafe path segments',
                    ));
                }
                if (!Object.hasOwn(override, 'value')) {
                    issues.push(issue(`${path}.value`, 'required', 'override value is required'));
                }
            }
        });
    }
    requireObject(value.semanticTokens, '$.semanticTokens', issues);
    requireObject(value.themeOverrides, '$.themeOverrides', issues);
    return finish(issues, options, 'Invalid authored overlay');
}

export function validateReimportReport(value, options = {}) {
    const issues = [];
    if (!isObject(value)) {
        issues.push(issue('$', 'type', 'reimport report must be an object'));
        return finish(issues, options, 'Invalid reimport report');
    }
    exact(value.$schema, REIMPORT_REPORT_SCHEMA, '$.$schema', issues);
    exact(value.schemaVersion, REIMPORT_REPORT_VERSION, '$.schemaVersion', issues);
    if (typeof value.accepted !== 'boolean') {
        issues.push(issue('$.accepted', 'type', 'accepted must be boolean'));
    }
    string(value.sourceKey, '$.sourceKey', issues);
    string(value.previousRevision, '$.previousRevision', issues);
    string(value.nextRevision, '$.nextRevision', issues);
    for (const field of ['authoredOverlay', 'changes', 'summary']) {
        requireObject(value[field], `$.${field}`, issues);
    }
    for (const field of ['aliasesApplied', 'conflicts', 'heuristicCandidates']) {
        if (!Array.isArray(value[field])) {
            issues.push(issue(`$.${field}`, 'type', `${field} must be an array`));
        }
    }
    if (isObject(value.summary)) {
        for (const [key, count] of Object.entries(value.summary)) {
            if (!Number.isInteger(count) || count < 0) {
                issues.push(issue(`$.summary.${key}`, 'range', 'summary values must be non-negative integers'));
            }
        }
    }
    return finish(issues, options, 'Invalid reimport report');
}

export function parseDesignIR(text) {
    const value = typeof text === 'string' ? parseJson(text, 'DesignIR') : text;
    validateDesignIR(value, { throwOnError: true });
    return value;
}

export function parseAuthoredOverlay(text) {
    const value = typeof text === 'string' ? parseJson(text, 'authored overlay') : text;
    validateAuthoredOverlay(value, { throwOnError: true });
    return value;
}

export function parseReimportReport(text) {
    const value = typeof text === 'string' ? parseJson(text, 'reimport report') : text;
    validateReimportReport(value, { throwOnError: true });
    return value;
}

function validateNode(node, path, sourceKey, issues, schemaVersion) {
    requireObject(node, path, issues);
    if (!isObject(node)) return;
    string(node.id, `${path}.id`, issues);
    if (typeof node.id === 'string' && sourceKey && !node.id.startsWith(`${sourceKey}/`)) {
        issues.push(issue(`${path}.id`, 'namespace', `node id must begin with '${sourceKey}/'`));
    }
    string(node.kind, `${path}.kind`, issues);
    requireObject(node.identity, `${path}.identity`, issues);
    if (isObject(node.identity)) {
        if (!['provider', 'semantic', 'structural'].includes(node.identity.strategy)) {
            issues.push(issue(
                `${path}.identity.strategy`,
                'enum',
                'identity strategy must be provider, semantic, or structural',
            ));
        } else if (node.identity.strategy === 'provider') {
            string(node.identity.sourceId, `${path}.identity.sourceId`, issues);
        } else if (node.identity.strategy === 'semantic') {
            string(node.identity.semanticId, `${path}.identity.semanticId`, issues);
        } else {
            string(node.identity.fingerprint, `${path}.identity.fingerprint`, issues);
            if (!Number.isInteger(node.identity.ordinal) || node.identity.ordinal < 0) {
                issues.push(issue(
                    `${path}.identity.ordinal`,
                    'range',
                    'structural identity ordinal must be a non-negative integer',
                ));
            }
        }
    }
    requireObject(node.properties, `${path}.properties`, issues);
    if (node.tracks !== undefined) {
        if (!(schemaVersion >= DESIGN_IR_TRACKS_VERSION)) {
            issues.push(issue(`${path}.tracks`, 'version', `tracks need schemaVersion ${DESIGN_IR_TRACKS_VERSION}`));
        }
        validateTracks(node.tracks, `${path}.tracks`, issues);
    }
    if (node.material !== undefined) {
        if (!(schemaVersion >= DESIGN_IR_MATERIALS_VERSION)) {
            issues.push(issue(
                `${path}.material`,
                'version',
                `a material reference needs schemaVersion ${DESIGN_IR_MATERIALS_VERSION} or later`,
            ));
        }
        validateMaterialReference(node.material, `${path}.material`, issues);
    }
    if (!Array.isArray(node.children)) {
        issues.push(issue(`${path}.children`, 'type', 'children must be an array'));
    } else {
        node.children.forEach((child, index) =>
            validateNode(child, `${path}.children[${index}]`, sourceKey, issues, schemaVersion),
        );
        validateMaskReferences(node, path, issues);
    }
    if (node.compositing !== undefined) {
        if (!(schemaVersion >= DESIGN_IR_COMPOSITING_VERSION)) {
            issues.push(issue(
                `${path}.compositing`,
                'version',
                `compositing needs schemaVersion ${DESIGN_IR_COMPOSITING_VERSION} or later`,
            ));
        } else {
            issues.push(...compositingProblems(node.compositing, `${path}.compositing`));
        }
    }
}

// A `maskRef` names the node that masks it: a sibling that is itself a mask,
// never the node itself, and a mask does not reference another mask.
function validateMaskReferences(parent, path, issues) {
    const byId = new Map();
    parent.children.forEach((child) => {
        if (isObject(child) && typeof child.id === 'string') byId.set(child.id, child);
    });
    parent.children.forEach((child, index) => {
        const reference = isObject(child) && isObject(child.compositing) ? child.compositing.maskRef : undefined;
        if (typeof reference !== 'string' || !reference) return;
        const where = `${path}.children[${index}].compositing.maskRef`;
        const target = byId.get(reference);
        if (reference === child.id) {
            issues.push(issue(where, 'mask', 'a node cannot be its own mask'));
        } else if (!target) {
            issues.push(issue(where, 'mask', `maskRef '${reference}' is not a sibling of this node`));
        } else if (!isObject(target.compositing) || target.compositing.mask === undefined) {
            issues.push(issue(where, 'mask', `maskRef '${reference}' does not name a mask node`));
        } else if (child.compositing.mask !== undefined) {
            issues.push(issue(where, 'mask', 'a mask node cannot reference another mask'));
        }
    });
}

const ANIMATED_PROPERTIES = new Set([
    'opacity', 'translateX', 'translateY', 'scaleX', 'scaleY', 'rotation',
    'fill', 'strokeColor', 'shadowColor', 'gradientStopColor', 'textRunColor',
]);
const COLOR_PROPERTIES = new Set([
    'fill', 'strokeColor', 'shadowColor', 'gradientStopColor', 'textRunColor',
]);
const INDEXED_PROPERTIES = new Set(['shadowColor', 'gradientStopColor', 'textRunColor']);
const FLOAT_MAX = 3.4028234663852886e38;
const SPRING_REST_THRESHOLD = 1e-4;
const EASING_KEYWORDS = new Set(['linear', 'ease', 'ease-in', 'ease-out', 'ease-in-out']);
const HEX_COLOR = /^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$/;

// Animation tracks carry keyframes only; the host clock that evaluates them
// is passed per frame and never stored in the document.
function validateTracks(tracks, path, issues) {
    if (!Array.isArray(tracks)) {
        issues.push(issue(path, 'type', 'tracks must be an array'));
        return;
    }
    if (tracks.length > 32) issues.push(issue(path, 'range', 'a node has at most 32 tracks'));
    const seen = new Set();
    tracks.forEach((track, index) => {
        const trackPath = `${path}[${index}]`;
        requireObject(track, trackPath, issues);
        if (!isObject(track)) return;
        for (const key of Object.keys(track)) {
            if (!['property', 'index', 'stop', 'keyframes'].includes(key)) {
                issues.push(issue(`${trackPath}.${key}`, 'unknown-field', 'a track carries property, index, stop and keyframes'));
            }
        }
        if (!ANIMATED_PROPERTIES.has(track.property)) {
            issues.push(issue(`${trackPath}.property`, 'enum', 'track property is not an animatable property'));
        }
        for (const field of ['index', 'stop']) {
            const value = track[field];
            if (value === undefined) continue;
            const used = field === 'index' ? INDEXED_PROPERTIES.has(track.property) : track.property === 'gradientStopColor';
            if (!used) {
                issues.push(issue(`${trackPath}.${field}`, 'unknown-field', `a ${track.property} track takes no ${field}`));
            } else if (!Number.isInteger(value) || value < 0 || value > 65535) {
                issues.push(issue(`${trackPath}.${field}`, 'range', `${field} must be an integer from 0 to 65535`));
            }
        }
        const address = `${track.property}/${track.index ?? 0}/${track.stop ?? 0}`;
        if (seen.has(address)) {
            issues.push(issue(trackPath, 'duplicate', 'two tracks animate the same property'));
        }
        seen.add(address);
        if (!Array.isArray(track.keyframes) || track.keyframes.length === 0 || track.keyframes.length > 256) {
            issues.push(issue(`${trackPath}.keyframes`, 'type', 'keyframes must be an array of 1 to 256 keyframes'));
            return;
        }
        const color = COLOR_PROPERTIES.has(track.property);
        let previous = -Infinity;
        track.keyframes.forEach((keyframe, keyIndex) => {
            const keyPath = `${trackPath}.keyframes[${keyIndex}]`;
            requireObject(keyframe, keyPath, issues);
            if (!isObject(keyframe)) return;
            for (const key of Object.keys(keyframe)) {
                if (!['time', 'value', 'easing'].includes(key)) {
                    issues.push(issue(`${keyPath}.${key}`, 'unknown-field', 'a keyframe carries time, value and easing'));
                }
            }
            if (typeof keyframe.time !== 'number' || !Number.isFinite(keyframe.time) || Math.abs(keyframe.time) > FLOAT_MAX) {
                issues.push(issue(`${keyPath}.time`, 'type', 'keyframe time must be a finite number of seconds within float range'));
            } else if (!(keyframe.time > previous)) {
                issues.push(issue(`${keyPath}.time`, 'order', 'keyframe times must strictly increase'));
            }
            if (typeof keyframe.time === 'number') previous = keyframe.time;
            const valid = color
                ? typeof keyframe.value === 'string' && HEX_COLOR.test(keyframe.value)
                : typeof keyframe.value === 'number' && Number.isFinite(keyframe.value) && Math.abs(keyframe.value) <= FLOAT_MAX;
            if (!valid) {
                issues.push(issue(
                    `${keyPath}.value`,
                    'type',
                    color ? 'a colour keyframe value is #rrggbb or #rrggbbaa' : 'a keyframe value is a finite number within float range',
                ));
            }
            if (keyframe.easing !== undefined) validateEasing(keyframe.easing, `${keyPath}.easing`, issues);
            const next = track.keyframes[keyIndex + 1];
            if (isObject(keyframe.easing) && keyframe.easing.type === 'spring' && isObject(next) &&
                typeof next.time === 'number' && typeof keyframe.time === 'number' &&
                next.time - keyframe.time < springSettleTime(keyframe.easing)) {
                issues.push(issue(`${keyPath}.easing`, 'range', 'the next keyframe comes before this spring settles'));
            }
        });
    });
}

// The time after release from which a spring's displacement and speed stay
// below the rest threshold, computed as the native renderer computes it from
// single-precision constants. Infinite for constants that never settle.
function springSettleTime(easing) {
    const constant = (value, fallback) => Math.fround(typeof value === 'number' ? value : fallback);
    const stiffness = constant(easing.stiffness, 100);
    const damping = constant(easing.damping, 10);
    const mass = constant(easing.mass, 1);
    const velocity = constant(easing.velocity, 0);
    if (!(stiffness > 0 && damping > 0 && mass > 0) || !Number.isFinite(velocity)) return Infinity;
    const omega = Math.sqrt(stiffness / mass);
    const zeta = damping / (2 * Math.sqrt(stiffness * mass));
    const start = -1;
    let bound;
    let rate;
    if (Math.abs(zeta - 1) <= 1e-6) {
        const b = velocity + omega * start;
        const reach = 2 / (Math.E * omega);
        bound = Math.max(Math.abs(start) + Math.abs(b) * reach, Math.abs(b - omega * start) + omega * Math.abs(b) * reach);
        rate = omega / 2;
    } else if (zeta < 1) {
        const damped = omega * Math.sqrt(1 - zeta * zeta);
        const b = (velocity + zeta * omega * start) / damped;
        bound = Math.hypot(start, b) * Math.max(1, omega);
        rate = zeta * omega;
    } else {
        const root = Math.sqrt(zeta * zeta - 1);
        const slow = -omega * (zeta - root);
        const fast = -omega * (zeta + root);
        const c2 = (velocity - slow * start) / (fast - slow);
        const c1 = start - c2;
        bound = Math.max(Math.abs(c1) + Math.abs(c2), Math.abs(c1 * slow) + Math.abs(c2 * fast));
        rate = -slow;
    }
    if (!Number.isFinite(bound) || !Number.isFinite(rate) || rate <= 0) return Infinity;
    if (bound <= SPRING_REST_THRESHOLD) return 0;
    return Math.log(bound / SPRING_REST_THRESHOLD) / rate;
}

function validateEasing(easing, path, issues) {
    if (typeof easing === 'string') {
        if (!EASING_KEYWORDS.has(easing)) {
            issues.push(issue(path, 'enum', 'easing keyword must be linear, ease, ease-in, ease-out or ease-in-out'));
        }
        return;
    }
    requireObject(easing, path, issues);
    if (!isObject(easing)) return;
    const finite = (value) => typeof value === 'number' && Number.isFinite(value) && Math.abs(value) <= FLOAT_MAX;
    const fields = {
        cubicBezier: ['type', 'x1', 'y1', 'x2', 'y2'],
        steps: ['type', 'steps', 'position'],
        spring: ['type', 'stiffness', 'damping', 'mass', 'velocity'],
    };
    const allowed = Object.hasOwn(fields, easing.type) ? fields[easing.type] : undefined;
    if (allowed === undefined) {
        issues.push(issue(`${path}.type`, 'enum', 'easing type must be cubicBezier, steps or spring'));
        return;
    }
    for (const key of Object.keys(easing)) {
        if (!allowed.includes(key)) {
            issues.push(issue(`${path}.${key}`, 'unknown-field', `${easing.type} easing does not take ${key}`));
        }
    }
    if (easing.type === 'cubicBezier') {
        const unit = (value) => finite(value) && value >= 0 && value <= 1;
        if (!unit(easing.x1) || !unit(easing.x2) || !finite(easing.y1) || !finite(easing.y2)) {
            issues.push(issue(path, 'range', 'cubicBezier needs x1 and x2 in [0, 1] and y1 and y2 within float range'));
        }
    } else if (easing.type === 'steps') {
        if (!Number.isInteger(easing.steps) || easing.steps < 1 || easing.steps > 65535 ||
            (easing.position === 'none' && easing.steps < 2)) {
            issues.push(issue(`${path}.steps`, 'range', 'steps must be an integer from 1, or 2 with position none, to 65535'));
        }
        if (easing.position !== undefined && !['start', 'end', 'none', 'both'].includes(easing.position)) {
            issues.push(issue(`${path}.position`, 'enum', 'steps position must be start, end, none or both'));
        }
    } else {
        for (const field of ['stiffness', 'damping', 'mass']) {
            if (easing[field] !== undefined && !(finite(easing[field]) && easing[field] > 0)) {
                issues.push(issue(`${path}.${field}`, 'range', `spring ${field} must be positive and within float range`));
            }
        }
        if (easing.velocity !== undefined && !finite(easing.velocity)) {
            issues.push(issue(`${path}.velocity`, 'type', 'spring velocity must be finite and within float range'));
        }
    }
}

// A material reference carries an id and values only: the uniforms' types,
// ranges and defaults belong to the material's registration.
function validateMaterialReference(material, path, issues) {
    requireObject(material, path, issues);
    if (!isObject(material)) return;
    for (const key of Object.keys(material)) {
        if (key !== 'id' && key !== 'values') {
            issues.push(issue(`${path}.${key}`, 'unknown-field', 'a material reference carries only id and values'));
        }
    }
    if (typeof material.id !== 'string' || !/^[a-z0-9][a-z0-9.-]*$/.test(material.id)) {
        issues.push(issue(`${path}.id`, 'format', 'material id must be lowercase letters, digits, dots and hyphens'));
    }
    requireObject(material.values, `${path}.values`, issues);
    if (!isObject(material.values)) return;
    for (const [name, value] of Object.entries(material.values)) {
        const valuePath = `${path}.values.${name}`;
        if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) {
            issues.push(issue(valuePath, 'format', 'uniform name must be an identifier'));
        }
        if (!isMaterialValue(value)) {
            issues.push(issue(
                valuePath,
                'type',
                'a material value is a number, two numbers, or a red, green, blue and alpha colour',
            ));
        }
    }
}

function isMaterialValue(value) {
    const finite = (number) => typeof number === 'number' && Number.isFinite(number);
    if (finite(value)) return true;
    if (Array.isArray(value)) return value.length === 2 && value.every(finite);
    if (!isObject(value)) return false;
    const keys = Object.keys(value).sort();
    return keys.join(',') === 'alpha,blue,green,red' && keys.every((key) => finite(value[key]));
}

function validateDiagnostic(diagnostic, path, issues) {
    requireObject(diagnostic, path, issues);
    if (!isObject(diagnostic)) return;
    string(diagnostic.code, `${path}.code`, issues);
    string(diagnostic.message, `${path}.message`, issues);
    string(diagnostic.path, `${path}.path`, issues);
    if (!DISPOSITIONS.includes(diagnostic.disposition)) {
        issues.push(issue(`${path}.disposition`, 'enum', 'unknown conversion disposition'));
    }
    if (!SEVERITIES.includes(diagnostic.severity)) {
        issues.push(issue(`${path}.severity`, 'enum', 'unknown diagnostic severity'));
    }
}

function finish(issues, options, message) {
    const result = { issues, valid: issues.length === 0 };
    if (!result.valid && options.throwOnError) throw new DesignIRValidationError(message, issues);
    return result;
}

function parseJson(text, label) {
    try {
        return JSON.parse(text);
    } catch (error) {
        throw new DesignIRValidationError(`${label} is not valid JSON: ${error.message}`);
    }
}

function requireObject(value, path, issues) {
    if (!isObject(value)) issues.push(issue(path, 'type', `${path} must be an object`));
}

function string(value, path, issues) {
    if (typeof value !== 'string' || !value) {
        issues.push(issue(path, 'type', `${path} must be a non-empty string`));
    }
}

function exact(actual, expected, path, issues) {
    if (actual !== expected) issues.push(issue(path, 'version', `${path} must equal ${expected}`));
}

function issue(path, code, message) {
    return { code, message, path };
}

function validateIdentityNamespace(value, prefix, path, issues) {
    if (typeof value === 'string' && prefix && !value.startsWith(prefix)) {
        issues.push(issue(path, 'namespace', `${path} must begin with '${prefix}'`));
    }
}

function isSafeOverridePath(value) {
    if (typeof value !== 'string') return false;
    const segments = value.split('.');
    if (segments.length < 2 || segments[0] !== 'properties') return false;
    return segments.slice(1).every((segment) =>
        /^[A-Za-z_][A-Za-z0-9_-]*$/.test(segment) &&
        !['__proto__', 'constructor', 'prototype'].includes(segment),
    );
}

function isObject(value) {
    return Boolean(value && typeof value === 'object' && !Array.isArray(value));
}
