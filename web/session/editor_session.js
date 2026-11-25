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
// `id` and a `kind`:
//   `knob`    bound to `param`, a parameter id; a vertical drag changes it.
//   `slider`  bound to `param`; the value follows the pointer along the
//             `<id>/track` box from the press on, with `label`, `unit` and
//             `decimals` drawn in the `<id>/name` and `<id>/value` boxes.
//   `toggle`  bound to `param`; a press flips it as one gesture.
//   `label`   static `text`, or `format(state)` of the session's feeds.
//   `button`  draws a chip, with `text` when it has one, and runs
//             `view.actions[action](session)` on press.
//   `shape`   a filled box in the palette `color`.
//   `plot`    draws the `feed` named `spectrum` or `ir`.
// `layout` returns {[key]: {x, y, width, height}} in CSS pixels, one entry
// per node id plus any sub-boxes the view names. A view may also supply
// `colors` to override the default palette, and `textStyle(key)` returning
// the style of a box: {fontSize, align, uppercase, tracking, advance} for
// text, where `align` is left, right or center and `tracking` draws each
// glyph `advance(glyph)` plus `tracking` ems after the last, and {radius} for
// a chip or shape. Colours are `#rrggbb` or `#rrggbbaa`.
//
// Any node may also have:
//   `vector(box, state)`  a vector asset ({viewBox, ops}, in the form the
//                         shell's `addVectorAsset` takes) drawn in the box
//                         before the node's own paint; a button or toggle
//                         with one draws no fill of its own.
//   `colorFor(state)`     a palette name for a label, replacing `color`.
//   `action`              on a node of any kind, pressing its box runs
//                         `view.actions[action](session)` and repaints.
// `state` is {impulseResponseName, time, value(param)}.
//
// A view with `backdrop(width, height, state)` returns backdrop marks (see
// the core's `backdrop_marks.hpp`) drawn behind every node. A view with
// `animated` repaints on every animation frame, and its clock, `state.time`,
// advances 1/60 s per frame before the frame is drawn, as the native editor's
// repaint clock does; `freezeTime(t)` stops it at `t` for a capture.

import {addVectorAsset} from '../shell/vector-canvas.js';
import {createCanvas2DBackend} from './canvas2d_backend.js';
import {sha256} from './content_hash.js';
import {GestureTracker, clampToParameter, trackValue} from './gesture_tracker.js';

const INK_DISTANCE = 8;
const DEFAULT_COLORS = {
    knob: '#1e293b', knobFill: '#14b8a6', knobText: '#e2e8f0',
    label: '#f8fafc', labelDim: '#94a3b8', value: '#f8fafc', plot: '#111827', plotFill: '#38bdf8',
    track: '#1e293b', trackFill: '#14b8a6', thumb: '#f8fafc',
    button: '#1e293b', buttonBorder: null, buttonText: '#14b8a6', sliderName: '#94a3b8',
    toggleOff: '#1e293b', toggleOn: '#f59e0b', toggleText: '#f8fafc', toggleTextOn: '#0b1020',
};
const BOUND_KINDS = new Set(['knob', 'slider', 'toggle']);
const PRESS_KINDS = new Set(['knob', 'slider', 'toggle', 'button']);
// The animation clock's step: one native repaint.
const FRAME_SECONDS = 1 / 60;
// Average advance of a glyph as a fraction of the font size, used to align
// text without measuring it.
const ADVANCE = 0.52;

function rgba(hex) {
    const source = /^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$/.test(hex) ? hex : '#000000';
    return [1, 3, 5, 7].map(offset => source.length > offset
        ? Number.parseInt(source.slice(offset, offset + 2), 16) / 255 : 1);
}

function wrapCore(module) {
    return {
        module,
        setBackdrop: module.cwrap('vellum_web_set_backdrop', 'number', ['number', 'number']),
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
    const COLORS = {...DEFAULT_COLORS, ...(view.colors || {})};
    const nodes = view.nodes.map(node => ({...node}));
    for (const node of nodes) {
        if (BOUND_KINDS.has(node.kind) && !infos.has(node.param)) {
            throw new Error(`editor control ${node.id} names an absent parameter ${node.param}`);
        }
    }
    const values = new Map();
    for (const node of nodes) {
        if (BOUND_KINDS.has(node.kind)) {
            values.set(node.param, await adapter.getParameterValue(node.param));
        }
    }

    const core = wrapCore(await createCore());
    if (!core.start()) throw new Error('the shared Wasm core did not start');
    // The backdrop's staging buffer in the Wasm heap, grown on demand.
    let backdropBuffer = {pointer: 0, floats: 0};
    // Content hashes of the vector assets already built, by their JSON.
    const vectorHashes = new Map();

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
    let time = 0;
    let frozen = false;
    // Draw ids left out of the frame being drawn, and whether its nodes and
    // its backdrop are drawn at all; see `snapshot`.
    let omit = {ids: new Set(), nodes: true, backdrop: true};
    // A shape draws unless its id is left out.
    const rect = (id, ...rest) => {
        if (!omit.ids.has(id)) core.rectangle(id, ...rest);
    };
    // The string each text box showed in the last frame, by box key.
    let drawnText = {};
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
        // The observer runs after layout and before paint, so drawing now
        // shows the new size in this frame instead of stretching the old one.
        if (frameRequest) window.cancelAnimationFrame(frameRequest);
        frameRequest = 0;
        paint();
    }

    function scheduleRepaint() {
        if (destroyed || frameRequest) return;
        frameRequest = window.requestAnimationFrame(() => {
            frameRequest = 0;
            if (view.animated && !frozen) time += FRAME_SECONDS;
            paint();
            if (view.animated && !frozen) scheduleRepaint();
        });
    }

    function viewState() {
        return {
            impulseResponseName: feeds.ir?.name ?? null,
            time,
            value: param => values.get(param),
        };
    }

    function contentHash(json) {
        let hash = vectorHashes.get(json);
        if (!hash) {
            hash = `sha256:${sha256(json)}`;
            vectorHashes.set(json, hash);
        }
        return hash;
    }

    function drawVector(id, box, asset) {
        // An asset with nothing in it draws nothing.
        if (!asset.ops.length || omit.ids.has(id)) return;
        const json = JSON.stringify([asset.viewBox, asset.ops]);
        addVectorAsset(core.module, id, box.x, box.y, box.width, box.height,
            {...asset, contentHash: contentHash(json)});
    }

    function setBackdrop() {
        if (!omit.backdrop) return;
        const marks = view.backdrop?.(size.cssWidth, size.cssHeight, viewState())?.marks;
        if (!marks?.length) return;
        if (backdropBuffer.floats < marks.length) {
            if (backdropBuffer.pointer) core.module._free(backdropBuffer.pointer);
            backdropBuffer = {pointer: core.module._malloc(marks.length * 4), floats: marks.length};
        }
        core.module.HEAPF32.set(marks, backdropBuffer.pointer >> 2);
        if (!core.setBackdrop(backdropBuffer.pointer, marks.length)) {
            throw new Error('the shared Wasm core refused the backdrop');
        }
    }

    function drawText(key, text, box, fontSize, color) {
        if (omit.ids.has(key)) return;
        const style = view.textStyle?.(key) ?? {fontSize, align: 'left'};
        if (style.uppercase) text = text.toUpperCase();
        const size = Math.max(1, style.fontSize);
        drawnText[key] = text;
        if (style.tracking !== undefined) {
            // One glyph at a time, each `advance(glyph)` ems plus the tracking
            // after the last.
            let x = box.x;
            [...text].forEach((glyph, index) => {
                core.text(`${key}/${index}`, glyph, x, box.y,
                    Math.max(0, box.x + box.width - x), box.height, size, ...rgba(color));
                x += size * (style.advance(glyph) + style.tracking);
            });
            return;
        }
        const slack = box.width - text.length * size * ADVANCE;
        let x = box.x;
        if (style.align === 'right') x += Math.max(0, slack);
        else if (style.align === 'center') x += Math.max(0, slack / 2);
        core.text(key, text, x, box.y, Math.max(0, box.x + box.width - x), box.height,
            size, ...rgba(color));
    }

    function fractionOf(node) {
        const info = infos.get(node.param);
        const minimum = Number.isFinite(info.minValue) ? info.minValue : 0;
        const maximum = Number.isFinite(info.maxValue) ? info.maxValue : 1;
        const fraction = maximum > minimum ? (values.get(node.param) - minimum) / (maximum - minimum) : 0;
        return Math.min(1, Math.max(0, fraction));
    }

    function paintNode(node, box) {
        const state = viewState();
        if (node.vector) drawVector(node.id, box, node.vector(box, state));
        if (node.kind === 'label') {
            const text = node.format ? String(node.format(state)) : node.text || '';
            const color = node.colorFor ? node.colorFor(state) : node.color;
            drawText(node.id, text, box, node.fontSize || 16, COLORS[color] || COLORS.label);
        } else if (node.kind === 'vector') {
            // The vector above is the whole node.
        } else if (node.kind === 'button' && node.vector) {
            if (node.text) {
                drawText(`${node.id}/text`, node.text, bounds[`${node.id}/text`] || box,
                    13, COLORS.buttonText);
            }
        } else if (node.kind === 'button') {
            const radius = view.textStyle?.(node.id)?.radius ?? box.height * 0.16;
            if (COLORS.buttonBorder) {
                // A one pixel border: the border color, then the fill inset.
                rect(`${node.id}/border`, box.x, box.y, box.width, box.height,
                    radius, ...rgba(COLORS.buttonBorder));
                rect(node.id, box.x + 1, box.y + 1, Math.max(0, box.width - 2),
                    Math.max(0, box.height - 2), Math.max(0, radius - 1), ...rgba(COLORS.button));
            } else {
                rect(node.id, box.x, box.y, box.width, box.height,
                    radius, ...rgba(COLORS.button));
            }
            if (node.text) {
                drawText(`${node.id}/text`, node.text, bounds[`${node.id}/text`] || box,
                    13, COLORS.buttonText);
            }
        } else if (node.kind === 'shape') {
            rect(node.id, box.x, box.y, box.width, box.height,
                view.textStyle?.(node.id)?.radius ?? 0, ...rgba(COLORS[node.color] || COLORS.label));
        } else if (node.kind === 'slider') {
            const track = bounds[`${node.id}/track`];
            const value = values.get(node.param);
            const unit = node.unit ? ` ${node.unit}` : '';
            if (bounds[`${node.id}/name`]) {
                drawText(`${node.id}/name`, node.label || '', bounds[`${node.id}/name`], 10,
                    COLORS.sliderName);
            }
            if (bounds[`${node.id}/value`]) {
                drawText(`${node.id}/value`, `${value.toFixed(node.decimals ?? 2)}${unit}`,
                    bounds[`${node.id}/value`], 14, COLORS.value);
            }
            if (track) {
                const radius = track.height / 2;
                const handle = track.x + fractionOf(node) * track.width;
                const thumb = track.height * (tracker.activeParam === node.param ? 2 : 1.75);
                rect(`${node.id}/track`, track.x, track.y, track.width, track.height,
                    radius, ...rgba(COLORS.track));
                rect(`${node.id}/fill`, track.x, track.y, handle - track.x, track.height,
                    radius, ...rgba(COLORS.trackFill));
                rect(`${node.id}/thumb`, handle - thumb, track.y + radius - thumb,
                    2 * thumb, 2 * thumb, thumb, ...rgba(COLORS.thumbRing || COLORS.thumb));
                if (COLORS.thumbRing) {
                    // A ring: the thumb disc inside a border of the track's height.
                    const inner = thumb - track.height / 2;
                    rect(`${node.id}/thumb-disc`, handle - inner,
                        track.y + radius - inner, 2 * inner, 2 * inner, inner,
                        ...rgba(COLORS.thumb));
                }
            }
        } else if (node.kind === 'toggle') {
            const on = values.get(node.param) >= 0.5;
            if (!node.vector) {
                rect(node.id, box.x, box.y, box.width, box.height, box.height / 2,
                    ...rgba(on ? COLORS.toggleOn : COLORS.toggleOff));
            }
            drawText(`${node.id}/text`, node.label || '', bounds[`${node.id}/text`] || box, 13,
                on ? COLORS.toggleTextOn : COLORS.toggleText);
        } else if (node.kind === 'knob') {
            const info = infos.get(node.param);
            const minimum = Number.isFinite(info.minValue) ? info.minValue : 0;
            const maximum = Number.isFinite(info.maxValue) ? info.maxValue : 1;
            const value = values.get(node.param);
            const fraction = maximum > minimum ? (value - minimum) / (maximum - minimum) : 0;
            const filled = box.height * Math.min(1, Math.max(0, fraction));
            rect(node.id, box.x, box.y, box.width, box.height, 8, ...rgba(COLORS.knob));
            rect(`${node.id}/value`, box.x, box.y + box.height - filled,
                box.width, filled, 8, ...rgba(COLORS.knobFill));
            core.text(`${node.id}/label`, info.label || '', box.x + 8, box.y + 6,
                Math.max(0, box.width - 16), 18, 13, ...rgba(COLORS.knobText));
        } else if (node.kind === 'plot') {
            rect(node.id, box.x, box.y, box.width, box.height, 4, ...rgba(COLORS.plot));
            const feed = feeds[node.feed];
            const samples = feed?.samples || [];
            const count = Math.min(samples.length, 64);
            for (let index = 0; index < count; ++index) {
                const level = Math.min(1, Math.abs(samples[Math.floor(index * samples.length / count)]));
                const width = box.width / count;
                rect(`${node.id}/bin${index}`, box.x + index * width,
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
        drawnText = {};
        setBackdrop();
        if (omit.nodes) {
            for (const node of nodes) {
                if (bounds[node.id]) paintNode(node, bounds[node.id]);
            }
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
            text: {...drawnText}, time,
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
            if ((PRESS_KINDS.has(node.kind) || node.action) && box &&
                point.x >= box.x && point.y >= box.y &&
                point.x <= box.x + box.width && point.y <= box.y + box.height) {
                return node;
            }
        }
        return null;
    }

    const listeners = {
        pointerdown(event) {
            const point = locate(event);
            const node = hit(point);
            if (!node) return;
            if (node.kind === 'button' || (node.action && !BOUND_KINDS.has(node.kind))) {
                event.preventDefault();
                view.actions?.[node.action]?.(session);
                scheduleRepaint();
                return;
            }
            const info = infos.get(node.param);
            if (node.kind === 'toggle') {
                // A press flips the toggle as one complete gesture.
                const flipped = values.get(node.param) >= 0.5 ? 0 : 1;
                if (!tracker.begin(event.pointerId, node.id, info, values.get(node.param), 0,
                    () => flipped)) {
                    return;
                }
                event.preventDefault();
                const value = tracker.move(event.pointerId, 0);
                if (value !== null) values.set(node.param, value);
                tracker.end(event.pointerId);
                scheduleRepaint();
                return;
            }
            if (node.kind === 'slider') {
                const track = bounds[`${node.id}/track`];
                if (!track || !tracker.begin(event.pointerId, node.id, info,
                    values.get(node.param), point.x, x => trackValue(info, track, x))) {
                    return;
                }
                event.preventDefault();
                canvas.setPointerCapture(event.pointerId);
                // The press itself sets the value under the pointer.
                const value = tracker.move(event.pointerId, point.x);
                if (value !== null) values.set(node.param, value);
                scheduleRepaint();
                return;
            }
            if (!tracker.begin(event.pointerId, node.id, info, values.get(node.param), point.y)) {
                return;
            }
            event.preventDefault();
            canvas.setPointerCapture(event.pointerId);
        },
        pointermove(event) {
            const active = tracker.active;
            if (!active || active.pointerId !== event.pointerId) return;
            // A handler may have remounted the view and dropped the target.
            const target = nodes.find(node => node.id === active.target);
            if (!target) {
                tracker.abandon();
                return;
            }
            const point = locate(event);
            const value = tracker.move(event.pointerId,
                target.kind === 'slider' ? point.x : point.y);
            if (value !== null) {
                values.set(active.info.id, value);
                scheduleRepaint();
            }
        },
        pointerup(event) {
            if (tracker.end(event.pointerId)) scheduleRepaint();
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
    // A device-pixel-ratio change with no CSS size change does not always
    // reach the observer: headless Chrome reports an emulated ratio change
    // only as a window resize, and a monitor or zoom change in a desktop
    // browser arrives as a resolution query change. Both re-measure. Each
    // query matches one ratio and is re-armed for the next.
    let scaleQuery = null;
    function watchScale() {
        scaleQuery?.removeEventListener('change', onScaleChange);
        scaleQuery = window.matchMedia(`(resolution: ${window.devicePixelRatio || 1}dppx)`);
        scaleQuery.addEventListener('change', onScaleChange);
    }
    function onScaleChange() {
        applySize(measureElement());
        watchScale();
    }
    watchScale();
    window.addEventListener('resize', onScaleChange);
    let fileRequest = null;

    const session = {
        get size() { return {...size}; },
        get bounds() { return structuredClone(bounds); },
        get stats() { return structuredClone(stats); },
        get gestures() { return gestures.map(event => ({...event})); },
        get pendingFrame() { return frameRequest !== 0; },
        get time() { return time; },
        // Stops the animation clock at `t` seconds and draws that frame now;
        // `null` starts it again from there.
        freezeTime(t) {
            if (t === null) {
                frozen = false;
                scheduleRepaint();
                return;
            }
            frozen = true;
            time = t;
            if (frameRequest) window.cancelAnimationFrame(frameRequest);
            frameRequest = 0;
            paint();
        },
        // Draws the current frame and returns the pixels of `region` (a box
        // in CSS pixels, or the whole canvas), then draws the full frame
        // again. `omit` lists draw ids to leave out: a text box key, or the id
        // of a shape or vector (a slider draws `<id>/track`, `<id>/fill`,
        // `<id>/thumb` and `<id>/thumb-disc`). `nodes: false` draws no node
        // and `backdrop: false` no backdrop. With the clock frozen, two
        // snapshots differ only in what they leave out.
        snapshot({omit: ids = [], nodes: drawNodes = true, backdrop = true, region = null} = {}) {
            omit = {ids: new Set(ids), nodes: drawNodes, backdrop};
            try {
                paint();
                const sx = size.backingWidth / size.cssWidth;
                const sy = size.backingHeight / size.cssHeight;
                const box = region ?? {x: 0, y: 0, width: size.cssWidth, height: size.cssHeight};
                const left = Math.max(0, Math.floor(box.x * sx));
                const top = Math.max(0, Math.floor(box.y * sy));
                const right = Math.min(canvas.width, Math.ceil((box.x + box.width) * sx));
                const bottom = Math.min(canvas.height, Math.ceil((box.y + box.height) * sy));
                return canvas.getContext('2d').getImageData(left, top,
                    Math.max(1, right - left), Math.max(1, bottom - top));
            } finally {
                omit = {ids: new Set(), nodes: true, backdrop: true};
                paint();
            }
        },
        requestRepaint() { scheduleRepaint(); },
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
        // The drawn canvas, with ink per laid-out box: the pixels farther than
        // INK_DISTANCE in RGB from the view background.
        capture() {
            const context = canvas.getContext('2d');
            const background = rgba(view.background).map(value => value * 255);
            const ink = {};
            for (const [key, box] of Object.entries(bounds)) {
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
                ink[key] = {pixels, area: width * height};
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
            scaleQuery.removeEventListener('change', onScaleChange);
            window.removeEventListener('resize', onScaleChange);
            for (const [type, listener] of Object.entries(listeners)) {
                canvas.removeEventListener(type, listener);
            }
            adapter.onParamsChanged = previousParams ?? null;
            if (hadIrFeed) adapter.onIrChanged = previousIr;
            fileRequest?.settle(null);
            canvas.remove();
            if (backdropBuffer.pointer) core.module._free(backdropBuffer.pointer);
            core.stop();
        },
    };

    bounds = view.layout(size.cssWidth, size.cssHeight);
    paint();
    if (view.animated) scheduleRepaint();
    return session;
}
