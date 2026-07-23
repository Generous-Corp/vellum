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

const componentDocument = await fetch('./vellum_components.json').then(response => {
    if (!response.ok) throw new Error(`component manifest request failed: ${response.status}`);
    return response.json();
});
if (componentDocument.schema !== 'vellum.web-components.v1' ||
    !Array.isArray(componentDocument.components)) {
    throw new Error('unsupported web component manifest');
}
const componentDeclarations = new Map();
const componentModules = new Map();
const componentEvidence = new Map();
for (const declaration of componentDocument.components) {
    if (!declaration || typeof declaration.id !== 'string' ||
        componentDeclarations.has(declaration.id) ||
        !['fallback', 'wasm'].includes(declaration.web)) {
        throw new Error('invalid or duplicate web component declaration');
    }
    componentDeclarations.set(declaration.id, declaration);
    if (declaration.web !== 'wasm') continue;
    if (typeof declaration.module !== 'string' || typeof declaration.wasm !== 'string') {
        throw new Error(`Wasm component payload is incomplete: ${declaration.id}`);
    }
    const imported = await import(`./${declaration.module}`);
    const instance = await imported.default({
        locateFile(path) {
            return path.endsWith('.wasm') ? `./${declaration.wasm}` : path;
        },
    });
    const componentApi = {
        start: instance.cwrap('vellum_component_web_start', 'number', ['string']),
        render: instance.cwrap(
            'vellum_component_web_render', 'number',
            ['string', 'string', 'number', 'number'],
        ),
        count: instance.cwrap('vellum_component_web_command_count', 'number', []),
        kind: instance.cwrap('vellum_component_web_command_kind', 'number', ['number']),
        suffix: instance.cwrap('vellum_component_web_command_suffix', 'string', ['number']),
        number: instance.cwrap(
            'vellum_component_web_command_number', 'number', ['number', 'number'],
        ),
        text: instance.cwrap('vellum_component_web_command_text', 'string', ['number']),
        error: instance.cwrap('vellum_component_web_error', 'string', []),
    };
    if (!componentApi.start(declaration.id)) {
        throw new Error(
            `Wasm component descriptor failed: ${declaration.id}: ${componentApi.error()}`,
        );
    }
    componentModules.set(declaration.id, componentApi);
    componentEvidence.set(declaration.id, {
        id: declaration.id, loaded: true, renders: 0, commands: 0,
    });
}

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
let scrollContainers = [];
function emitCustomNode(node, proposed, absoluteX, absoluteY, componentApi) {
    const properties = JSON.stringify(node.properties || {});
    if (!componentApi.render(node.id, properties, proposed.width, proposed.height)) {
        throw new Error(`Wasm component render failed: ${node.component}: ${componentApi.error()}`);
    }
    const count = componentApi.count();
    if (!(count > 0 && count <= 4096)) {
        throw new Error(`Wasm component emitted an invalid command count: ${node.component}`);
    }
    for (let index = 0; index < count; ++index) {
        const kind = componentApi.kind(index);
        const suffix = componentApi.suffix(index);
        const values = Array.from({length: 10}, (_, field) => componentApi.number(index, field));
        const id = `${node.id}/custom/${suffix}`;
        if (kind === 1) {
            api.rectangle(
                id, absoluteX + values[0], absoluteY + values[1], values[2], values[3],
                values[8], values[4], values[5], values[6], values[7],
            );
        } else if (kind === 2) {
            api.text(
                id, componentApi.text(index), absoluteX + values[0], absoluteY + values[1],
                values[2], values[3], values[9],
                values[4], values[5], values[6], values[7],
            );
        } else {
            throw new Error(`Wasm component emitted an unsupported command: ${node.component}`);
        }
    }
    const evidence = componentEvidence.get(node.component);
    evidence.renders += 1;
    evidence.commands += count;
}

function lowerNode(node, proposed, parentX, parentY) {
    const style = node.style || {};
    const absoluteX = parentX + proposed.x;
    const absoluteY = parentY + proposed.y;
    if (node.scroll) {
        scrollContainers.push({id: node.id, direction: node.scroll});
    }
    if (node.type === 'custom') {
        const declaration = componentDeclarations.get(node.component);
        if (!declaration) throw new Error(`custom component is undeclared: ${node.component}`);
        if (declaration.web === 'wasm') {
            emitCustomNode(
                node, proposed, absoluteX, absoluteY, componentModules.get(node.component),
            );
            return;
        }
    } else if (node.type === 'text' || node.type === 'text-run') {
        api.text(node.id, directText(node), absoluteX, absoluteY, proposed.width, proposed.height,
            Math.max(1, number(style, 'fontSize', 14)), ...color(style.color, '#111827'));
    } else if (node.type === 'text-input') {
        api.rectangle(
            node.id, absoluteX, absoluteY, proposed.width, proposed.height,
            Math.max(6, number(style, 'borderRadius', 6)),
            ...color(style.backgroundColor, '#FFFFFF'),
        );
        api.text(
            `${node.id}/value`, node.value || node.placeholder || '',
            absoluteX + 12, absoluteY + 10, Math.max(0, proposed.width - 24),
            Math.max(0, proposed.height - 20),
            Math.max(1, number(style, 'fontSize', 14)), ...color(style.color, '#111827'),
        );
    } else if (node.type === 'button' || style.backgroundColor) {
        const fill = node.type === 'button' && !style.backgroundColor ? '#14b8a6' : style.backgroundColor;
        api.rectangle(node.id, absoluteX, absoluteY, proposed.width, proposed.height,
            Math.max(0, number(style, 'borderRadius', node.type === 'button' ? 10 : 0)), ...color(fill, '#000000'));
    }
    if (Object.keys(node.events || {}).length > 0) {
        interactions.push({
            id: node.id, type: node.type, value: node.value || '', events: node.events,
            x: absoluteX, y: absoluteY, width: proposed.width, height: proposed.height,
        });
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
            ? number(childStyle, 'fontSize', 14) * 1.4
            : child.type === 'button' || child.type === 'text-input' ? 48 : 64);
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
    scrollContainers = [];
    lowerNode(tree, {x: 0, y: 0, width, height}, 0, 0);
    if (!api.render()) throw new Error('Wasm did not render');
    return {digest: api.digest() >>> 0, commandCount: api.count(), width, height, tree};
}

if (!api.start()) throw new Error('shared C++ runtime did not start');
globalThis.__vellumHostV2 = Object.freeze({
    invalidateJSON() {
        requestAnimationFrame(() => {
            if (bridge.isDirty()) current = render();
        });
    },
});
let current = render();
canvas.addEventListener('click', event => {
    const rect = canvas.getBoundingClientRect();
    const scale = globalThis.devicePixelRatio || 1;
    const x = (event.clientX - rect.left) * canvas.width / rect.width / scale;
    const y = (event.clientY - rect.top) * canvas.height / rect.height / scale;
    const hit = [...interactions].reverse().find(item => x >= item.x && y >= item.y &&
        x <= item.x + item.width && y <= item.y + item.height);
    if (hit?.events?.press) {
        bridge.dispatchJSON(JSON.stringify({
            protocol: bridge.protocol, action: hit.events.press, payload: null,
        }));
        current = render();
    }
});

function dispatch(action, payload) {
    bridge.dispatchJSON(JSON.stringify({protocol: bridge.protocol, action, payload}));
    current = render();
}

function interaction(target, type = null) {
    const value = interactions.find(item => item.id === target && (type === null || item.type === type));
    if (!value) throw new Error(`scenario target not found: ${target}`);
    return value;
}

function withoutLastGrapheme(value) {
    if (!value) return '';
    if (typeof Intl.Segmenter === 'function') {
        const segments = [...new Intl.Segmenter(undefined, {granularity: 'grapheme'}).segment(value)];
        return value.slice(0, segments.at(-1).index);
    }
    return Array.from(value).slice(0, -1).join('');
}

async function runScenario(path) {
    const response = await fetch(path);
    if (!response.ok) throw new Error(`scenario request failed: ${response.status}`);
    const scenario = await response.json();
    if (!['vellum.scenario.v1', 'vellum.scenario.v2'].includes(scenario.schema) ||
        !Array.isArray(scenario.steps) ||
        (scenario.schema === 'vellum.scenario.v1' &&
         (!scenario.viewport || !Number.isInteger(scenario.viewport.width) ||
          !Number.isInteger(scenario.viewport.height)))) {
        throw new Error('unsupported scenario document');
    }
    if (scenario.viewport &&
        (current.width !== scenario.viewport.width || current.height !== scenario.viewport.height)) {
        throw new Error(
            `scenario viewport mismatch: expected ${scenario.viewport.width}x` +
            `${scenario.viewport.height}, rendered ${current.width}x${current.height}`,
        );
    }
    const initial = current;
    const captures = [];
    const presses = [];
    const inputs = [];
    const keys = [];
    const touches = [];
    const assertions = [];
    const services = [];
    const throws = [];
    function findNode(node, target) {
        if (node.id === target) return node;
        for (const child of node.children || []) {
            const found = findNode(child, target);
            if (found) return found;
        }
        return null;
    }
    function nodeText(node) {
        if (!node) return '';
        if (typeof node.text === 'string') return node.text;
        if (typeof node.value === 'string') return node.value;
        return (node.children || []).map(nodeText).join('');
    }
    for (const step of scenario.steps) {
        if (step.action === 'wait-for-idle') {
            await new Promise(resolve => requestAnimationFrame(() =>
                requestAnimationFrame(resolve)));
            if (bridge.isDirty()) current = render();
        } else if (step.action === 'capture') {
            captures.push({name: step.name || step.value || 'capture', ...current});
        } else if (step.action === 'press' || step.action === 'click') {
            const target = step.target || step.id;
            const hit = interaction(target);
            if (!hit.events.press) throw new Error(`scenario target is not pressable: ${target}`);
            const before = current.digest;
            dispatch(hit.events.press, null);
            presses.push({target: hit.id, before, after: current.digest, changed: before !== current.digest});
        } else if (step.action === 'touch') {
            const hit = interaction(step.target);
            if (!hit.events.press) {
                throw new Error(`scenario target is not touch-pressable: ${step.target}`);
            }
            const before = current.digest;
            dispatch(hit.events.press, step.event?.payload ?? {pointerType: 'touch'});
            touches.push({
                target: hit.id,
                before,
                after: current.digest,
                changed: before !== current.digest,
            });
        } else if (step.action === 'input') {
            const hit = interaction(step.target, 'text-input');
            if (!hit.events.change) throw new Error(`scenario target is not editable: ${step.target}`);
            const text = step.text ?? step.value;
            dispatch(hit.events.change, {value: text, inputType: 'scenario'});
            inputs.push({target: step.target, bytes: new TextEncoder().encode(text).length,
                executed: true});
        } else if (step.action === 'key') {
            let hit = interaction(step.target, 'text-input');
            let executed = false;
            const key = step.key ?? step.value;
            if (hit.events.keyDown) {
                dispatch(hit.events.keyDown, {key, repeat: false, source: 'scenario'});
                executed = true;
                hit = interaction(step.target, 'text-input');
            }
            if (key === 'Enter' && hit.events.submit) {
                dispatch(hit.events.submit, {value: hit.value, source: 'scenario'});
                executed = true;
                hit = interaction(step.target, 'text-input');
            }
            if (key === 'Backspace' && hit.events.change) {
                dispatch(hit.events.change, {
                    value: withoutLastGrapheme(hit.value), inputType: 'scenario',
                });
                executed = true;
            }
            if (!executed) {
                throw new Error(`text input has no handler for semantic key: ${key}`);
            }
            keys.push({target: step.target, key, executed: true});
        } else if (step.action === 'assert-text') {
            const actual = nodeText(findNode(current.tree, step.target));
            if (!actual.includes(String(step.expect))) {
                throw new Error(
                    `scenario text assertion failed for ${step.target}: ${actual}`,
                );
            }
            assertions.push({action: step.action, target: step.target, passed: true});
        } else if (step.action === 'command') {
            if (!bridge.hasCommand(step.target)) {
                throw new Error(`scenario command is not defined: ${step.target}`);
            }
            assertions.push({action: step.action, target: step.target, passed: true});
        } else if (step.action === 'service-result') {
            if (step.service?.ok === false && typeof step.service.error?.code === 'string') {
                globalThis.__vellumExpectedRejections ??= [];
                globalThis.__vellumExpectedRejections.push(step.service.error.code);
            }
            globalThis.__vellumServiceHost.responses.push(step.service);
            const hit = interaction(step.target);
            if (!hit.events.press) {
                throw new Error(`scenario service target is not pressable: ${step.target}`);
            }
            dispatch(hit.events.press, null);
            await Promise.resolve();
            services.push({
                target: step.target,
                requested: globalThis.__vellumServiceHost.requests.at(-1)?.service,
                supplied: true,
            });
        } else if (step.action === 'throw') {
            const hit = interaction(step.target);
            if (!hit.events.press) {
                throw new Error(`scenario throw target is not pressable: ${step.target}`);
            }
            let diagnostic;
            try {
                dispatch(hit.events.press, null);
            } catch (error) {
                diagnostic = typeof globalThis.__vellumMapExceptionJSON === 'function'
                    ? JSON.parse(globalThis.__vellumMapExceptionJSON(error)) : null;
            }
            if (!diagnostic ||
                !JSON.stringify(diagnostic).includes(String(step.expect))) {
                throw new Error(`scenario expected mapped throw from ${step.target}`);
            }
            throws.push({target: step.target, diagnostic, passed: true});
        } else {
            throw new Error(`unsupported scenario action: ${step.action}`);
        }
    }
    if (presses.some(item => !item.changed)) {
        throw new Error('every semantic press must change rendered state');
    }
    return {schema: 'vellum.web-proof.v1', scenario: scenario.name, backend: api.backend(),
        authoringRuntime: 'browser JavaScript', initial, final: current,
        captures, presses, inputs, keys, touches, assertions, services, throws,
        scrollContainers, components: [...componentEvidence.values()],
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
