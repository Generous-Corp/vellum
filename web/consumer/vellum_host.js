import createVellumCore from './vellum_web_core.js';

const canvas = document.querySelector('#vellum-canvas');
const status = document.querySelector('#vellum-status');
const context = canvas.getContext('2d');

function cssColor(red, green, blue, alpha) {
    return `rgba(${Math.round(red * 255)},${Math.round(green * 255)},${Math.round(blue * 255)},${alpha})`;
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

if (!globalThis.__vellum || globalThis.__vellum.protocol !== 'vellum.authoring-host.v1') {
    throw new Error('app.js did not mount the vellum.authoring-host.v1 bridge');
}
const bridge = globalThis.__vellum;
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
    return [1, 3, 5].map(offset => Number.parseInt(source.slice(offset, offset + 2), 16) / 255).concat(1);
}
function number(style, key, fallback) { return typeof style?.[key] === 'number' ? style[key] : fallback; }
function directText(node) {
    if (typeof node.text === 'string') return node.text;
    return (node.children || []).filter(child => child.type === 'text-run').map(child => child.text || '').join('');
}

let interactions = [];
function lowerNode(node, proposed, parentX, parentY) {
    const style = node.style || {};
    const absoluteX = parentX + proposed.x;
    const absoluteY = parentY + proposed.y;
    if (node.type === 'text' || node.type === 'text-run') {
        api.text(node.id, directText(node), absoluteX, absoluteY, proposed.width, proposed.height,
            Math.max(1, number(style, 'fontSize', 14)), ...color(style.color, '#111827'));
    } else if (node.type === 'button' || style.backgroundColor) {
        const fill = node.type === 'button' && !style.backgroundColor ? '#14b8a6' : style.backgroundColor;
        api.rectangle(node.id, absoluteX, absoluteY, proposed.width, proposed.height,
            Math.max(0, number(style, 'borderRadius', node.type === 'button' ? 10 : 0)), ...color(fill, '#000000'));
    }
    for (const action of Object.values(node.events || {})) {
        interactions.push({ id: node.id, action, x: absoluteX, y: absoluteY,
            width: proposed.width, height: proposed.height });
    }
    if (node.type === 'button' && directText(node)) {
        api.text(`${node.id}/label`, directText(node), absoluteX + 16,
            absoluteY + (proposed.height - 19.6) * 0.5, Math.max(0, proposed.width - 32), 19.6,
            14, ...color(style.color, '#041412'));
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
        let height = number(childStyle, 'height', child.type === 'text' || child.type === 'text-run'
            ? number(childStyle, 'fontSize', 14) * 1.4 : child.type === 'button' ? 48 : 64);
        const x = number(childStyle, 'x', isStack && horizontal ? cursor : padding);
        const y = number(childStyle, 'y', isStack && !horizontal ? cursor : padding);
        if (width <= 0 && horizontal) width = Math.max(0, proposed.width - cursor - padding);
        if (height <= 0 && !horizontal) height = Math.max(0, proposed.height - cursor - padding);
        lowerNode(child, {x, y, width, height}, absoluteX, absoluteY);
        if (isStack) cursor += (horizontal ? width : height) + gap;
    }
}

function render() {
    const tree = JSON.parse(bridge.renderJSON()).tree;
    const style = tree.style || {};
    const width = number(style, 'width', 0);
    const height = number(style, 'height', 0);
    if (!(width > 0 && height > 0)) throw new Error('web root requires positive width and height');
    if (!api.begin(width, height, ...color(style.backgroundColor, '#f8fafc'))) throw new Error('Wasm rejected frame');
    interactions = [];
    lowerNode(tree, {x: 0, y: 0, width, height}, 0, 0);
    if (!api.render()) throw new Error('Wasm did not render');
    return {digest: api.digest() >>> 0, commandCount: api.count()};
}

if (!api.start()) throw new Error('shared C++ runtime did not start');
let current = render();
canvas.addEventListener('click', event => {
    const rect = canvas.getBoundingClientRect();
    const scale = globalThis.devicePixelRatio || 1;
    const x = (event.clientX - rect.left) * canvas.width / rect.width / scale;
    const y = (event.clientY - rect.top) * canvas.height / rect.height / scale;
    const hit = [...interactions].reverse().find(item => x >= item.x && y >= item.y &&
        x <= item.x + item.width && y <= item.y + item.height);
    if (hit) {
        bridge.dispatchJSON(JSON.stringify({protocol: bridge.protocol, action: hit.action, payload: null}));
        current = render();
    }
});

async function runScenario(path) {
    const response = await fetch(path);
    if (!response.ok) throw new Error(`scenario request failed: ${response.status}`);
    const scenario = await response.json();
    if (scenario.schema !== 'vellum.scenario.v1' || !Array.isArray(scenario.steps)) {
        throw new Error('unsupported scenario document');
    }
    const initial = current;
    const captures = [];
    const presses = [];
    for (const step of scenario.steps) {
        if (step.action === 'wait-for-idle') {
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        } else if (step.action === 'capture') {
            captures.push({name: step.name || 'capture', ...current});
        } else if (step.action === 'press' || step.action === 'click') {
            const hit = interactions.find(item => item.id === (step.target || step.id));
            if (!hit) throw new Error(`scenario target not found: ${step.target || step.id}`);
            const before = current.digest;
            bridge.dispatchJSON(JSON.stringify({protocol: bridge.protocol, action: hit.action, payload: null}));
            current = render();
            presses.push({target: hit.id, before, after: current.digest, changed: before !== current.digest});
        } else {
            throw new Error(`unsupported scenario action: ${step.action}`);
        }
    }
    if (presses.some(item => !item.changed)) {
        throw new Error('every semantic press must change rendered state');
    }
    return {schema: 'vellum.web-proof.v1', scenario: scenario.name, backend: api.backend(),
        authoringRuntime: 'browser JavaScript', initial, final: current, captures, presses,
        canvasDataBytes: canvas.toDataURL('image/png').length};
}

document.body.dataset.vellumReady = 'true';
document.body.dataset.vellumBackend = api.backend();
status.textContent = 'Vellum shared C++ Wasm core is running.';
const scenarioPath = new URL(location.href).searchParams.get('vellum-scenario');
if (scenarioPath) {
    const evidence = await runScenario(scenarioPath);
    await fetch('/__vellum_proof', {method: 'POST', headers: {'content-type':'application/json'},
        body: JSON.stringify(evidence)});
}
