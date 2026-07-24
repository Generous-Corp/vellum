import createVellumCore from './vellum_web_core.js';
import { Button, Stack, Text, mount, useState } from './vellum-ui/runtime.js';

const canvas = document.querySelector('#vellum-canvas');
const status = document.querySelector('#status');
const proof = document.querySelector('#proof');
const context = canvas.getContext('2d');

function cssColor(red, green, blue, alpha) {
    return `rgba(${Math.round(red * 255)},${Math.round(green * 255)},` +
        `${Math.round(blue * 255)},${alpha})`;
}

globalThis.VellumCanvasBackend = Object.freeze({
    begin(width, height, red, green, blue, alpha) {
        const scale = globalThis.devicePixelRatio || 1;
        canvas.width = Math.round(width * scale);
        canvas.height = Math.round(height * scale);
        canvas.style.aspectRatio = `${width} / ${height}`;
        context.setTransform(scale, 0, 0, scale, 0, 0);
        context.fillStyle = cssColor(red, green, blue, alpha);
        context.fillRect(0, 0, width, height);
    },
    rectangle(x, y, width, height, radius, red, green, blue, alpha) {
        context.beginPath();
        context.roundRect(x, y, width, height, Math.max(0, radius));
        context.fillStyle = cssColor(red, green, blue, alpha);
        context.fill();
    },
    text(text, x, baseline, size, red, green, blue, alpha) {
        context.font = `${Math.max(1, size)}px system-ui, sans-serif`;
        context.textBaseline = 'alphabetic';
        context.fillStyle = cssColor(red, green, blue, alpha);
        context.fillText(text, x, baseline);
    },
    finish() {},
});

function Application() {
    const [count, setCount] = useState(0);
    return Stack({
        id: 'proof-root',
        style: { width: 640, height: 400, padding: 32, gap: 18, backgroundColor: '#0f172a' },
        children: [
            Text({
                id: 'title', text: 'Vellum without a browser UI runtime',
                style: { height: 44, fontSize: 28, color: '#f8fafc' },
            }),
            Text({
                id: 'description', text: 'Browser JavaScript drives the shared C++ core compiled to Wasm.',
                style: { height: 34, fontSize: 16, color: '#94a3b8' },
            }),
            Button({
                id: 'increment', onPress: () => setCount((value) => value + 1),
                style: { width: 230, height: 58, backgroundColor: '#14b8a6', borderRadius: 14, color: '#042f2e' },
                children: `Wasm count: ${count}`,
            }),
        ],
    });
}

const bridge = mount(Application);
const module = await createVellumCore();
const api = {
    start: module.cwrap('vellum_web_start', 'number', []),
    begin: module.cwrap('vellum_web_begin_frame', 'number', ['number','number','number','number','number','number']),
    rectangle: module.cwrap('vellum_web_add_rectangle', 'number', ['string','number','number','number','number','number','number','number','number','number']),
    text: module.cwrap('vellum_web_add_text', 'number', ['string','string','number','number','number','number','number','number','number','number','number']),
    render: module.cwrap('vellum_web_render', 'number', []),
    count: module.cwrap('vellum_web_command_count', 'number', []),
    digest: module.cwrap('vellum_web_command_digest', 'number', []),
    backend: module.cwrap('vellum_web_backend_name', 'string', []),
};

function color(value, fallback) {
    const source = typeof value === 'string' && /^#[0-9a-fA-F]{6}$/.test(value) ? value : fallback;
    return [1, 3, 5].map((offset) => Number.parseInt(source.slice(offset, offset + 2), 16) / 255).concat(1);
}

function number(style, key, fallback) {
    return typeof style?.[key] === 'number' ? style[key] : fallback;
}

// Mirrors the retained-tree layout contract used by the native materializer.
// The parity test fails if this maintained browser proof drifts as well.
const DEFAULT_BUTTON_HEIGHT = 44;
const DEFAULT_TEXT_INPUT_HEIGHT = 44;
const DEFAULT_GENERIC_HEIGHT = 0;
const TEXT_LINE_HEIGHT_MULTIPLIER = 1.4;
function defaultHeight(type, style) {
    if (type === 'text' || type === 'text-run') {
        return number(style, 'fontSize', 14) * TEXT_LINE_HEIGHT_MULTIPLIER;
    }
    if (type === 'button') return DEFAULT_BUTTON_HEIGHT;
    if (type === 'text-input') return DEFAULT_TEXT_INPUT_HEIGHT;
    return DEFAULT_GENERIC_HEIGHT;
}

function directText(node) {
    if (typeof node.text === 'string') return node.text;
    return (node.children || []).filter((child) => child.type === 'text-run')
        .map((child) => child.text || '').join('');
}

let interactions = [];
function lowerNode(node, proposed, parentX, parentY) {
    const style = node.style || {};
    const absoluteX = parentX + proposed.x;
    const absoluteY = parentY + proposed.y;
    const fill = color(style.backgroundColor, '#000000');
    if (node.type === 'text' || node.type === 'text-run') {
        const foreground = color(style.color, '#111827');
        api.text(node.id, directText(node), absoluteX, absoluteY, proposed.width,
            proposed.height, Math.max(1, number(style, 'fontSize', 14)), ...foreground);
    } else if (node.type === 'button' || style.backgroundColor) {
        const background = node.type === 'button' && !style.backgroundColor
            ? color('#14b8a6', '#14b8a6') : fill;
        api.rectangle(node.id, absoluteX, absoluteY, proposed.width, proposed.height,
            Math.max(0, number(style, 'borderRadius', node.type === 'button' ? 10 : 0)), ...background);
    }
    for (const action of Object.values(node.events || {})) {
        interactions.push({ action, x: absoluteX, y: absoluteY, width: proposed.width, height: proposed.height });
    }
    if (node.type === 'button') {
        const label = directText(node);
        if (label) {
            const foreground = color(style.color, '#041412');
            api.text(`${node.id}/label`, label, absoluteX + 16,
                absoluteY + (proposed.height - 19.6) * 0.5,
                Math.max(0, proposed.width - 32), 19.6, 14, ...foreground);
        }
    }
    const isStack = node.type === 'stack';
    const horizontal = style.direction === 'horizontal';
    const padding = Math.max(0, number(style, 'padding', 0));
    const gap = Math.max(0, number(style, 'gap', 0));
    let cursor = padding;
    for (const child of node.children || []) {
        if (child.type === 'text-run' && (node.type === 'text' || node.type === 'button')) continue;
        const childStyle = child.style || {};
        let width = number(childStyle, 'width', horizontal ? 0 : Math.max(0, proposed.width - padding * 2));
        let height = number(childStyle, 'height', defaultHeight(child.type, childStyle));
        const x = number(childStyle, 'x', isStack && horizontal ? cursor : padding);
        const y = number(childStyle, 'y', isStack && !horizontal ? cursor : padding);
        if (width <= 0 && horizontal) width = Math.max(0, proposed.width - cursor - padding);
        if (height <= 0 && !horizontal) height = Math.max(0, proposed.height - cursor - padding);
        lowerNode(child, { x, y, width, height }, absoluteX, absoluteY);
        if (isStack) cursor += (horizontal ? width : height) + gap;
    }
}

function render() {
    const tree = JSON.parse(bridge.renderJSON()).tree;
    const style = tree.style || {};
    const width = number(style, 'width', 0);
    const height = number(style, 'height', 0);
    if (!(width > 0 && height > 0)) throw new Error('browser proof requires positive root width and height');
    const background = color(style.backgroundColor, '#f8fafc');
    if (!api.begin(width, height, ...background)) throw new Error('Wasm core rejected the frame');
    interactions = [];
    lowerNode(tree, { x: 0, y: 0, width, height }, 0, 0);
    if (!api.render()) throw new Error('Wasm core did not render');
    return { digest: api.digest() >>> 0, commandCount: api.count() };
}

if (!api.start()) throw new Error('shared C++ runtime did not start');
const initial = render();
const firstAction = interactions[0]?.action;
if (!firstAction) throw new Error('demo produced no semantic interaction');
bridge.dispatchJSON(JSON.stringify({ protocol: bridge.protocol, action: firstAction, payload: null }));
const afterAction = render();
if (initial.digest === afterAction.digest) throw new Error('interaction did not change shared-core output');

canvas.addEventListener('click', (event) => {
    const rect = canvas.getBoundingClientRect();
    const x = (event.clientX - rect.left) * canvas.width / rect.width / (globalThis.devicePixelRatio || 1);
    const y = (event.clientY - rect.top) * canvas.height / rect.height / (globalThis.devicePixelRatio || 1);
    const hit = [...interactions].reverse().find((item) =>
        x >= item.x && y >= item.y && x <= item.x + item.width && y <= item.y + item.height);
    if (hit) {
        bridge.dispatchJSON(JSON.stringify({ protocol: bridge.protocol, action: hit.action, payload: null }));
        render();
    }
});

const evidence = {
    backend: api.backend(),
    embeddedEngineMarkers: false,
    authoringRuntime: 'browser JavaScript',
    initial,
    afterAction,
    canvasDataBytes: canvas.toDataURL('image/png').length,
};
proof.textContent = JSON.stringify(evidence, null, 2);
status.textContent = 'Shared C++ Wasm core rendered and handled a state update.';
document.body.dataset.vellumReady = 'true';
document.body.dataset.vellumBackend = evidence.backend;
// The path is intentionally harmless outside the local verification server.
// It gives headless CI a bounded proof handshake after browser execution.
fetch('/__vellum_proof', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(evidence),
}).catch(() => {});
