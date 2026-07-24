import { createHash } from 'node:crypto';
import { isAbsolute } from 'node:path';
import process from 'node:process';
import { stableStringify } from '../src/index.js';

export const BACKEND_SCHEMA = 'vellum.backend.result.v1';
export const IMPORT_LOCK_SCHEMA = 'vellum.design-import-lock.v1';
export const IMPORT_GRAPH_SCHEMA = 'vellum.design-imports.v1';
export const IMPORT_REPORT_SCHEMA = 'vellum.design-import-report.v1';
export const ASSET_MANIFEST_SCHEMA = 'vellum.design-assets.v1';
export const GENERATED_COMPONENT_SCHEMA = 'vellum.generated-components.v1';
export const GENERATED_BINDINGS_SCHEMA = 'vellum.generated-bindings.v1';

export function parseArguments(values) {
    const output = { _: [] };
    for (let index = 0; index < values.length; index += 1) {
        const argument = values[index];
        if (!argument.startsWith('--')) {
            output._.push(argument);
            continue;
        }
        const key = argument.slice(2);
        if (key === 'json') {
            output.json = true;
            continue;
        }
        const value = values[index + 1];
        if (value === undefined || value.startsWith('--')) {
            fail('invalid_arguments', `--${key} requires a value`);
        }
        if (Object.hasOwn(output, key)) fail('invalid_arguments', `--${key} was provided twice`);
        output[key] = value;
        index += 1;
    }
    return output;
}

export function requiredSource(args) {
    const value = args.source ?? args._?.[0];
    if (!value) fail('invalid_arguments', '--source (or import positional source) is required');
    return value;
}

export function requestedSourceKey(args) {
    if (args.as !== undefined && args['source-key'] !== undefined) {
        fail('invalid_arguments', '--as and --source-key cannot be combined');
    }
    return validateSourceKey(args.as ?? args['source-key'] ?? 'main');
}

function validateSourceKey(value) {
    if (!/^[a-z][a-z0-9-]{0,63}$/.test(value)) {
        fail('invalid_source_key', 'Source key must be lowercase kebab-case and at most 64 characters');
    }
    return value;
}

export function sourceArchiveArguments(args) {
    const values = {
        member: args['source-archive-member'],
        name: args['source-archive-name'],
        path: args['source-archive'],
        sha256: args['source-archive-sha256'],
    };
    const present = Object.values(values).filter((value) => value !== undefined).length;
    if (present === 0) return null;
    if (present !== Object.keys(values).length) {
        fail('invalid_source_archive', 'Staged Pulp archive metadata is incomplete');
    }
    return values;
}

export function validateRevision(value) {
    if (typeof value !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/.test(value)) {
        fail('invalid_revision', 'Source revision must be a safe 1-80 character identifier');
    }
    return value;
}

export function isSafeRelativeAsset(value) {
    return typeof value === 'string' && value.length > 0 && !isAbsolute(value) &&
        !value.includes('\\') && !value.split('/').some((part) => ['', '.', '..'].includes(part));
}

export function success(status, message, data, diagnostics = []) {
    return { data, diagnostics, message, ok: true, schema: BACKEND_SCHEMA, status };
}

export function failure(status, message, data, diagnostics = [], exitCode = 1) {
    process.exitCode = exitCode;
    return { data, diagnostics, message, ok: false, schema: BACKEND_SCHEMA, status };
}

export function fail(status, message, options = {}) {
    const error = new Error(message);
    error.status = status;
    error.exitCode = options.exitCode ?? 1;
    error.diagnostics = options.diagnostics ?? [];
    throw error;
}

export function jsonBytes(value) {
    return Buffer.from(stableStringify(value), 'utf8');
}

export function sha256(bytes) {
    return createHash('sha256').update(bytes).digest('hex');
}

export function conflictsAsDiagnostics(conflicts) {
    return conflicts.map((conflict) => ({
        code: conflict.code ?? 'reimport-conflict',
        level: 'error',
        message: conflict.message ?? 'Reimport conflict',
        nodeId: conflict.nodeId ?? null,
    }));
}
