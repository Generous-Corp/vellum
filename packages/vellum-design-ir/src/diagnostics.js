import { DISPOSITIONS, SEVERITIES } from './constants.js';
import { canonicalize } from './stable-json.js';

export function normalizeDiagnostic(value, fallbackPath = '$') {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
        throw new TypeError('diagnostic must be an object');
    }
    const severity = SEVERITIES.includes(value.severity) ? value.severity : 'warning';
    const disposition = DISPOSITIONS.includes(value.disposition)
        ? value.disposition
        : 'unsupported';
    const code = nonEmptyString(value.code, 'diagnostic.code');
    const message = nonEmptyString(value.message, 'diagnostic.message');
    const diagnostic = {
        code,
        disposition,
        message,
        path: typeof value.path === 'string' && value.path ? value.path : fallbackPath,
        severity,
    };
    for (const field of ['property', 'remediation', 'sourceLocation']) {
        if (value[field] !== undefined) diagnostic[field] = canonicalize(value[field]);
    }
    if (value.detail !== undefined) diagnostic.detail = canonicalize(value.detail);
    return diagnostic;
}

export function diagnosticSort(left, right) {
    const leftKey = `${left.path}\u0000${left.code}\u0000${left.property ?? ''}`;
    const rightKey = `${right.path}\u0000${right.code}\u0000${right.property ?? ''}`;
    return leftKey.localeCompare(rightKey);
}

export function buildLossReport(diagnostics) {
    const byDisposition = Object.fromEntries(DISPOSITIONS.map((name) => [name, 0]));
    const bySeverity = Object.fromEntries(SEVERITIES.map((name) => [name, 0]));
    for (const diagnostic of diagnostics) {
        byDisposition[diagnostic.disposition] += 1;
        bySeverity[diagnostic.severity] += 1;
    }
    const losses = diagnostics.filter((diagnostic) =>
        ['rasterized', 'web-only', 'unsupported'].includes(diagnostic.disposition),
    );
    return {
        byDisposition,
        bySeverity,
        lossCount: losses.length,
        losses,
        totalDiagnostics: diagnostics.length,
    };
}

function nonEmptyString(value, field) {
    if (typeof value !== 'string' || !value.trim()) {
        throw new TypeError(`${field} must be a non-empty string`);
    }
    return value.trim();
}
