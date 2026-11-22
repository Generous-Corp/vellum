import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { normalizeImport } from '../src/index.js';
import { fail } from './backend-protocol.js';

const adapter = fileURLToPath(new URL('../../sdk/bin/vellum-source-adapter', import.meta.url));
const paintFields = [
    'backgroundColor', 'borderRadius', 'borderTopLeftRadius', 'borderTopRightRadius',
    'borderBottomRightRadius', 'borderBottomLeftRadius', 'borderTopWidth', 'borderRightWidth',
    'borderBottomWidth', 'borderLeftWidth', 'borderTopColor', 'borderRightColor',
    'borderBottomColor', 'borderLeftColor',
];
const textFields = [
    'color', 'fontFamily', 'fontSize', 'fontWeight', 'fontStyle', 'letterSpacing',
    'textAlign', 'verticalAlign',
];

export const nativeSourceTypes = new Set(['pencil', 'designmd']);

export function decodeNativeSource(bytes, { sourceKey, sourceHash, sourceType }) {
    const input = sourceType === 'pencil' ? pencilPayload(bytes) : bytes;
    const process = spawnSync(adapter, [sourceType], {
        input, encoding: 'utf8', timeout: 60_000, maxBuffer: 128 * 1024 * 1024,
    });
    if (process.error?.code === 'ENOENT') {
        fail('capability_unavailable', `${sourceType} imports require the native SDK source adapter`, { exitCode: 4 });
    }
    if (process.error || process.status !== 0) {
        fail(`invalid_${sourceType}`, `${sourceType} source adapter failed to complete`);
    }
    const [report, ir] = JSON.parse(process.stdout);
    const diagnostics = report.diagnostics
        .filter(({ code }) => sourceType !== 'designmd' || !code.startsWith('ir.'))
        .map(({ code, severity, message, ir_path }) => ({ code, severity, message, path: ir_path || '$' }));
    const toBackend = ({ severity, ...diagnostic }) => ({ ...diagnostic, level: severity });
    if (!ir || diagnostics.some(({ severity, code }) =>
        severity === 'error' || code === 'designmd.broken-ref')) {
        fail(`invalid_${sourceType}`, `${sourceType} source has invalid content`, {
            diagnostics: diagnostics.map(toBackend),
        });
    }
    const document = normalizeImport({
        source: {
            key: sourceKey,
            adapter: sourceType,
            adapterVersion: ir.source_version || '1',
            formatVersion: String(ir.version),
            revision: `${sourceType}-${sourceHash.slice(7)}`,
            snapshotHash: sourceHash,
        },
        root: irNode(ir.root, '$.root', diagnostics),
        tokens: irTokens(ir.tokens),
        diagnostics,
    });
    return { backendDiagnostics: diagnostics.map(toBackend), document };
}

function pencilPayload(bytes) {
    let document;
    try {
        document = JSON.parse(bytes.toString('utf8'));
    } catch {
        fail('invalid_pencil', 'Pencil source must contain valid JSON');
    }
    if (!document || typeof document !== 'object' || Array.isArray(document) ||
        (typeof document.type !== 'string' && !Array.isArray(document.children))) {
        fail('invalid_pencil', 'Pencil source must contain a node or document children');
    }
    const stack = [{ value: document, depth: 0 }];
    while (stack.length > 0) {
        const { value, depth } = stack.pop();
        if (depth > 256) fail('invalid_pencil', 'Pencil source exceeds the JSON nesting limit');
        if (value && typeof value === 'object') {
            for (const child of Object.values(value)) stack.push({ value: child, depth: depth + 1 });
        }
    }
    return JSON.stringify(document);
}

function fields(source, keys) {
    return Object.fromEntries(keys.filter((key) => source[key] !== undefined)
        .map((key) => [key, source[key]]));
}

function irNode(node, path, diagnostics) {
    const style = node.style ?? {};
    const layout = { ...node.layout, ...fields(style, ['width', 'height']) };
    if (style.left !== undefined) layout.x = style.left;
    if (style.top !== undefined) layout.y = style.top;
    const kind = node.type === 'label' ? 'text' : node.type;
    const supported = ['text', 'button', 'frame', 'rectangle', 'rect', 'group'].includes(kind);
    if (!supported) {
        diagnostics.push({
            code: 'pencil.unsupported-node', severity: 'warning', path,
            message: `Pencil node '${kind}' has no retained component mapping`,
        });
    }
    const paint = fields(style, paintFields);
    const text = fields(style, textFields);
    return {
        kind: kind === 'text' || kind === 'button' ? kind : 'view',
        name: node.name,
        sourceId: node.source_node_id,
        text: node.content,
        properties: { layout, paint, text },
        extensions: { native: { type: node.type, style } },
        children: (node.children ?? []).map((child, index) =>
            irNode(child, `${path}.children[${index}]`, diagnostics)),
    };
}

function irTokens(tokens) {
    const entries = [];
    for (const [group, type] of [['colors', 'color'], ['dimensions', 'dimension'], ['strings', 'string']]) {
        for (const [name, value] of Object.entries(tokens?.[group] ?? {})) {
            const tokenValue = type === 'dimension' ? { value, unit: 'px' } : value;
            entries.push([name, { $type: type, $value: tokenValue }]);
        }
    }
    return Object.fromEntries(entries);
}
