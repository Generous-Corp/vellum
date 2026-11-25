// The Vellum editor-host session: one plug-in editor mounted in a page.
//
// A session owns the editor canvas inside a page-supplied container. It
// measures the canvas in CSS pixels and device pixels, keeps a retained view,
// repaints on the next animation frame through the shared Wasm core's
// Canvas2D command path, routes pointer drags into parameter gestures, and
// bridges parameters, state, the impulse-response and spectrum feeds, and
// page file selection to the audio adapter. The adapter is the web player's
// `HostAdapter` (WAM or WebCLAP); the page owns it and its audio graph, and
// `destroy()` leaves it running.
//
// A view is {background, nodes, layout(width, height)}. Each node has an
// `id` and a `kind`: `knob` (bound to `param`, a parameter id), `label`
// (static `text`) or `plot` (draws the `feed` named `spectrum` or `ir`).
// `layout` returns {[id]: {x, y, width, height}} in CSS pixels.

import {createCanvas2DBackend} from './canvas2d_backend.js';
import {GestureTracker, clampToParameter} from './gesture_tracker.js';

const INK_DISTANCE = 8;
const COLORS = {
    knob: '#1e293b', knobFill: '#14b8a6', knobText: '#e2e8f0',
    label: '#f8fafc', plot: '#111827', plotFill: '#38bdf8',
};

function rgba(hex) {
    const source = /^#[0-9a-fA-F]{6}$/.test(hex) ? hex : '#000000';
    return [1, 3, 5].map(offset =>
        Number.parseInt(source.slice(offset, offset + 2), 16) / 255).concat(1);
}

function wrapCore(module) {
    return {
        start: module.cwrap('vellum_web_start', 'number', []),
        stop: module.cwrap('vellum_web_stop', 'number', []),
        setScale: module.cwrap('vellum_web_set_scale', 'number', ['number']),
        begin: module.cwrap('vellum_web_begin_frame', 'number',
            ['number', 'number', 'number', 'number', 'number', 'number']),
        rectangle: module.cwrap('vellum_web_add_rectangle', 'number',
            ['string', 'number', 'number', 'number', 'number', 'number',
                'number', 'number', 'number', 'number']),
        text: module.cwrap('vellum_web_add_text', 'number',
            ['string', 'string', 'number', 'number', 'number', 'number', 'number',
                'number', 'number', 'number', 'number']),
        render: module.cwrap('vellum_web_render', 'number', []),
        count: module.cwrap('vellum_web_command_count', 'number', []),
        digest: module.cwrap('vellum_web_command_digest', 'number', []),
    };
}

function sameSize(left, right) {
    return left.cssWidth === right.cssWidth && left.cssHeight === right.cssHeight &&
        left.scale === right.scale && left.backingWidth === right.backingWidth &&
        left.backingHeight === right.backingHeight;
}

// Mounts `view` into `container` and resolves once the first frame is drawn.
// `createCore` is the Emscripten factory of `vellum_web_core.js`.
export async function createEditorSession({container, adapter, view, createCore}) {
    const document = container.ownerDocument;
    const window = document.defaultView;
    const infos = new Map((await adapter.getParameterInfo()).map(info => [info.id, info]));
    const nodes = view.nodes.map(node => ({...node}));
    for (const node of nodes) {
        if (node.kind === 'knob' && !infos.has(node.param)) {
            throw new Error(`editor control ${node.id} names an absent parameter ${node.param}`);
        }
    }
    const values = new Map();
    for (const node of nodes) {
        if (node.kind === 'knob') {
            values.set(node.param, await adapter.getParameterValue(node.param));
        }
    }

    const core = wrapCore(await createCore());
    if (!core.start()) throw new Error('the shared Wasm core did not start');

    const canvas = document.createElement('canvas');
    canvas.style.cssText = 'display:block;width:100%;height:100%;touch-action:none';
    container.append(canvas);

    let size = measureElement();
    let bounds = {};
    let frameRequest = 0;
    let destroyed = false;
    const feeds = {ir: null, spectrum: null};
    const gestures = [];
    const stats = {frames: 0, resizes: 0, lastFrame: null};
    const backend = createCanvas2DBackend(canvas, () => size);

    const tracker = new GestureTracker(event => {
        gestures.push(event);
        if (event.type === 'begin') adapter.beginGesture?.(event.param);
        else if (event.type === 'set') adapter.setParameterValue(event.param, event.value);
        else adapter.endGesture?.(event.param);
    });

    function measureElement() {
        const rect = canvas.getBoundingClientRect();
        const scale = window.devicePixelRatio || 1;
        return {
            cssWidth: rect.width, cssHeight: rect.height, scale,
            backingWidth: Math.max(1, Math.round(rect.width * scale)),
            backingHeight: Math.max(1, Math.round(rect.height * scale)),
        };
    }

    function measureEntry(entry) {
        const box = entry.contentBoxSize?.[0];
        const device = entry.devicePixelContentBoxSize?.[0];
        const cssWidth = box ? box.inlineSize : entry.contentRect.width;
        const cssHeight = box ? box.blockSize : entry.contentRect.height;
        const scale = window.devicePixelRatio || 1;
        return {
            cssWidth, cssHeight, scale,
            backingWidth: Math.max(1, device ? device.inlineSize : Math.round(cssWidth * scale)),
            backingHeight: Math.max(1, device ? device.blockSize : Math.round(cssHeight * scale)),
        };
    }

    function applySize(next) {
        if (!(next.cssWidth > 0 && next.cssHeight > 0) || sameSize(size, next)) return;
        size = next;
        stats.resizes += 1;
        bounds = view.layout(size.cssWidth, size.cssHeight);
        scheduleRepaint();
    }

    function scheduleRepaint() {
        if (destroyed || frameRequest) return;
        frameRequest = window.requestAnimationFrame(() => {
            frameRequest = 0;
            paint();
        });
    }

    function paintNode(node, box) {
        if (node.kind === 'label') {
            core.text(node.id, node.text || '', box.x, box.y, box.width, box.height,
                Math.max(1, node.fontSize || 16), ...rgba(COLORS.label));
        } else if (node.kind === 'knob') {
            const info = infos.get(node.param);
            const minimum = Number.isFinite(info.minValue) ? info.minValue : 0;
            const maximum = Number.isFinite(info.maxValue) ? info.maxValue : 1;
            const value = values.get(node.param);
            const fraction = maximum > minimum ? (value - minimum) / (maximum - minimum) : 0;
            const filled = box.height * Math.min(1, Math.max(0, fraction));
            core.rectangle(node.id, box.x, box.y, box.width, box.height, 8, ...rgba(COLORS.knob));
            core.rectangle(`${node.id}/value`, box.x, box.y + box.height - filled,
                box.width, filled, 8, ...rgba(COLORS.knobFill));
            core.text(`${node.id}/label`, info.label || '', box.x + 8, box.y + 6,
                Math.max(0, box.width - 16), 18, 13, ...rgba(COLORS.knobText));
        } else if (node.kind === 'plot') {
            core.rectangle(node.id, box.x, box.y, box.width, box.height, 4, ...rgba(COLORS.plot));
            const feed = feeds[node.feed];
            const samples = feed?.samples || [];
            const count = Math.min(samples.length, 64);
            for (let index = 0; index < count; ++index) {
                const level = Math.min(1, Math.abs(samples[Math.floor(index * samples.length / count)]));
                const width = box.width / count;
                core.rectangle(`${node.id}/bin${index}`, box.x + index * width,
                    box.y + box.height * (1 - level), Math.max(1, width - 1),
                    box.height * level, 0, ...rgba(COLORS.plotFill));
            }
        }
    }

    function paint() {
        if (destroyed) return;
        core.setScale(size.scale);
        if (!core.begin(size.cssWidth, size.cssHeight, ...rgba(view.background))) {
            throw new Error('the shared Wasm core rejected the frame');
        }
        for (const node of nodes) {
            if (bounds[node.id]) paintNode(node, bounds[node.id]);
        }
        const previous = globalThis.VellumCanvasBackend;
        globalThis.VellumCanvasBackend = backend;
        let rendered;
        try {
            rendered = core.render();
        } finally {
            globalThis.VellumCanvasBackend = previous;
        }
        if (!rendered) throw new Error('the shared Wasm core refused the frame');
        stats.frames += 1;
        stats.lastFrame = {
            digest: core.digest() >>> 0, commandCount: core.count(),
            cssWidth: size.cssWidth, cssHeight: size.cssHeight, scale: size.scale,
        };
    }

    function locate(event) {
        const rect = canvas.getBoundingClientRect();
        return {
            x: (event.clientX - rect.left) * size.cssWidth / rect.width,
            y: (event.clientY - rect.top) * size.cssHeight / rect.height,
        };
    }

    function hit(point) {
        for (let index = nodes.length - 1; index >= 0; --index) {
            const node = nodes[index];
            const box = bounds[node.id];
            if (node.kind === 'knob' && box && point.x >= box.x && point.y >= box.y &&
                point.x <= box.x + box.width && point.y <= box.y + box.height) {
                return node;
            }
        }
        return null;
    }

    const listeners = {
        pointerdown(event) {
            const node = hit(locate(event));
            if (!node) return;
            const info = infos.get(node.param);
            if (!tracker.begin(event.pointerId, node.id, info, values.get(node.param),
                locate(event).y)) {
                return;
            }
            event.preventDefault();
            canvas.setPointerCapture(event.pointerId);
        },
        pointermove(event) {
            const active = tracker.active;
            if (!active || active.pointerId !== event.pointerId) return;
            // A handler may have remounted the view and dropped the target.
            if (!nodes.some(node => node.id === active.target)) {
                tracker.abandon();
                return;
            }
            const value = tracker.move(event.pointerId, locate(event).y);
            if (value !== null) {
                values.set(active.info.id, value);
                scheduleRepaint();
            }
        },
        pointerup(event) {
            tracker.end(event.pointerId);
        },
        pointercancel(event) {
            tracker.cancel(event.pointerId);
        },
        lostpointercapture(event) {
            tracker.cancel(event.pointerId);
        },
    };
    for (const [type, listener] of Object.entries(listeners)) {
        canvas.addEventListener(type, listener);
    }

    const previousParams = adapter.onParamsChanged;
    adapter.onParamsChanged = (changed, params) => {
        params.forEach((info, index) => {
            if (infos.has(info.id) && info.id !== tracker.activeParam) {
                values.set(info.id, changed[index]);
            }
        });
        scheduleRepaint();
        previousParams?.(changed, params);
    };
    const hadIrFeed = 'onIrChanged' in adapter;
    const previousIr = hadIrFeed ? adapter.onIrChanged : null;
    if (hadIrFeed) {
        adapter.onIrChanged = ir => {
            session.feedImpulseResponse(ir);
            previousIr?.(ir);
        };
    }

    const observer = new window.ResizeObserver(entries => {
        for (const entry of entries) applySize(measureEntry(entry));
    });
    try {
        observer.observe(canvas, {box: 'device-pixel-content-box'});
    } catch {
        observer.observe(canvas);
    }
    let fileRequest = null;

    const session = {
        get size() { return {...size}; },
        get bounds() { return structuredClone(bounds); },
        get stats() { return structuredClone(stats); },
        get gestures() { return gestures.map(event => ({...event})); },
        get pendingFrame() { return frameRequest !== 0; },
        value(param) { return values.get(param); },
        feedImpulseResponse(ir) {
            feeds.ir = {name: String(ir?.name ?? ''), samples: Float32Array.from(ir?.samples ?? [])};
            scheduleRepaint();
        },
        feedSpectrum(bins) {
            feeds.spectrum = {samples: Float32Array.from(bins ?? [])};
            scheduleRepaint();
        },
        get impulseResponseName() { return feeds.ir?.name ?? null; },
        saveState() {
            return adapter.getState();
        },
        async loadState(bytes) {
            await adapter.setState(bytes);
            for (const param of values.keys()) {
                values.set(param, clampToParameter(infos.get(param),
                    await adapter.getParameterValue(param)));
            }
            scheduleRepaint();
        },
        // Opens the page's own file chooser; there is no native picker on the
        // web. Resolves with the chosen File, or null when the request is
        // superseded or the session is torn down.
        selectFile({accept = ''} = {}) {
            fileRequest?.settle(null);
            const input = document.createElement('input');
            input.type = 'file';
            input.accept = accept;
            input.hidden = true;
            container.append(input);
            return new Promise(resolve => {
                const request = {
                    input,
                    settle(file) {
                        if (fileRequest === request) fileRequest = null;
                        input.remove();
                        resolve(file);
                    },
                };
                fileRequest = request;
                input.addEventListener('change', () => request.settle(input.files?.[0] ?? null));
                input.addEventListener('cancel', () => request.settle(null));
                input.click();
            });
        },
        // The drawn canvas, with ink per node: the pixels farther than
        // INK_DISTANCE in RGB from the view background.
        capture() {
            const context = canvas.getContext('2d');
            const background = rgba(view.background).map(value => value * 255);
            const ink = {};
            for (const node of nodes) {
                const box = bounds[node.id];
                if (!box) continue;
                const left = Math.floor(box.x * size.backingWidth / size.cssWidth);
                const top = Math.floor(box.y * size.backingHeight / size.cssHeight);
                const width = Math.max(1, Math.ceil(box.width * size.backingWidth / size.cssWidth));
                const height = Math.max(1, Math.ceil(box.height * size.backingHeight / size.cssHeight));
                const data = context.getImageData(left, top, width, height).data;
                let pixels = 0;
                for (let index = 0; index < data.length; index += 4) {
                    const distance = Math.hypot(data[index] - background[0],
                        data[index + 1] - background[1], data[index + 2] - background[2]);
                    if (distance > INK_DISTANCE) pixels += 1;
                }
                ink[node.id] = {pixels, area: width * height};
            }
            return {
                png: canvas.toDataURL('image/png'),
                width: canvas.width, height: canvas.height, scale: size.scale, ink,
            };
        },
        // Releases everything the session installed. Safe to call twice.
        destroy() {
            if (destroyed) return;
            tracker.abandon();
            destroyed = true;
            if (frameRequest) window.cancelAnimationFrame(frameRequest);
            frameRequest = 0;
            observer.disconnect();
            for (const [type, listener] of Object.entries(listeners)) {
                canvas.removeEventListener(type, listener);
            }
            adapter.onParamsChanged = previousParams ?? null;
            if (hadIrFeed) adapter.onIrChanged = previousIr;
            fileRequest?.settle(null);
            canvas.remove();
            core.stop();
        },
    };

    bounds = view.layout(size.cssWidth, size.cssHeight);
    paint();
    return session;
}
