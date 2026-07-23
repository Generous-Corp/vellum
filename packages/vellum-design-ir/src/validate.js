import {
    AUTHORED_OVERLAY_SCHEMA,
    AUTHORED_OVERLAY_VERSION,
    DESIGN_IR_SCHEMA,
    DESIGN_IR_VERSION,
    DISPOSITIONS,
    REIMPORT_REPORT_SCHEMA,
    REIMPORT_REPORT_VERSION,
    SEVERITIES,
} from './constants.js';
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
    exact(value.schemaVersion, DESIGN_IR_VERSION, '$.schemaVersion', issues);
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
    validateNode(value.root, '$.root', value.source?.key, issues);
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

function validateNode(node, path, sourceKey, issues) {
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
    if (!Array.isArray(node.children)) {
        issues.push(issue(`${path}.children`, 'type', 'children must be an array'));
    } else {
        node.children.forEach((child, index) =>
            validateNode(child, `${path}.children[${index}]`, sourceKey, issues),
        );
    }
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
