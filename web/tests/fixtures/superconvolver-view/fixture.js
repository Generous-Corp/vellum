// Mounts the Superconvolver view through the editor session against a
// recording stand-in for the web player's HostAdapter. The stand-in declares
// the plug-in's web parameter set and nothing else, records every read, and
// throws on a read of any other id, the way StateStore::get_value faults on
// an unregistered id in the wasm build. The browser harness drives real input
// through CDP and reads the results through `__editorFixture`.
import createVellumCore from './vellum_web_core.js';
import {createEditorSession} from './session/editor_session.js';
import {createSuperconvolverView} from './views/superconvolver.js';
import {reduceLuma, regionInk, ssim, verdict} from './perceptual_gate.js';

// The plug-in's web declarations (Pulp examples/super-convolver/super_convolver.hpp).
const params = [
    {id: 1, label: 'Mix', type: 'float', minValue: 0, maxValue: 100, defaultValue: 35, step: 0.1},
    {id: 2, label: 'Size', type: 'float', minValue: 0.05, maxValue: 4, defaultValue: 1.5},
    {id: 3, label: 'Gain', type: 'float', minValue: -24, maxValue: 24, defaultValue: 0},
    {id: 4, label: 'Bypass', type: 'boolean', minValue: 0, maxValue: 1, defaultValue: 0, step: 1},
];
const values = new Map();
const calls = [];
const reads = [];
const adapter = {
    descriptor: {name: 'Superconvolver view fixture', hasState: true},
    audioNode: null,
    onParamsChanged: null,
    async getParameterInfo() { return params.map(info => ({...info})); },
    async getParameterValue(id) {
        reads.push(id);
        if (!values.has(id)) throw new Error(`read of unregistered parameter ${id}`);
        return values.get(id);
    },
    setParameterValue(id, value) {
        if (!values.has(id)) throw new Error(`write of unregistered parameter ${id}`);
        calls.push({call: 'set', param: id, value});
        values.set(id, value);
        this.onParamsChanged?.(params.map(info => values.get(info.id)), params);
    },
    beginGesture(id) { calls.push({call: 'begin', param: id}); },
    endGesture(id) { calls.push({call: 'end', param: id}); },
    async getState() { return new Uint8Array(); },
    async setState() {},
};

function resetValues() {
    for (const info of params) values.set(info.id, info.defaultValue);
}

// A deterministic decaying impulse response and a sloped spectrum. The view
// draws neither, but both feeds reach the session the way the page will send
// them.
const impulse = Array.from({length: 256}, (_, index) =>
    Math.exp(-index / 60) * (index % 2 ? -1 : 1) * (0.6 + 0.4 * Math.sin(index * 0.7)));
const spectrum = Array.from({length: 64}, (_, index) => 0.9 - index / 90 + 0.1 * Math.sin(index));

const container = document.querySelector('#editor');
const frames = () => new Promise(resolve =>
    requestAnimationFrame(() => requestAnimationFrame(resolve)));
let session = null;
let view = null;
let fileRequests = [];

function report() {
    const canvas = container.querySelector('canvas');
    const rect = canvas.getBoundingClientRect();
    const infos = new Map(params.map(info => [info.id, info]));
    return {
        size: session.size,
        bounds: session.bounds,
        text: session.stats.lastFrame.text,
        mapping: view.nodes.filter(node => 'param' in node).map(node => ({
            id: node.id, kind: node.kind, param: node.param, name: node.label,
            adapterLabel: infos.get(node.param)?.label ?? null,
        })),
        nodes: view.nodes.map(node => node.id),
        reads: [...reads],
        calls: calls.map(call => ({...call})),
        values: Object.fromEntries(params.map(info => [info.id, session.value(info.id) ?? null])),
        fileRequests: fileRequests.map(request => ({accept: request.accept, settled: request.settled})),
        canvas: {left: rect.left, top: rect.top, cssWidth: rect.width, cssHeight: rect.height},
    };
}

// `field` replaces the field's {density, flow, energy}, `values` the
// parameter defaults, and `time` freezes the animation clock there.
async function mount(width, height, {field = {}, values: preset = {}, time = null} = {}) {
    session?.destroy();
    calls.length = 0;
    reads.length = 0;
    fileRequests = [];
    resetValues();
    for (const [id, value] of Object.entries(preset)) values.set(Number(id), value);
    container.style.width = `${width}px`;
    container.style.height = `${height}px`;
    view = createSuperconvolverView(await adapter.getParameterInfo(), {
        field,
        // The page's own file selection stands in for the native picker.
        onLoadImpulseResponse(active) {
            const request = {accept: '.wav,.aif,.aiff,.flac', settled: false};
            fileRequests.push(request);
            active.selectFile({accept: request.accept}).then(file => {
                request.settled = true;
                if (file) active.feedImpulseResponse({name: file.name, samples: impulse});
            });
        },
    });
    session = await createEditorSession({container, adapter, view, createCore: createVellumCore});
    session.feedImpulseResponse({name: '', samples: impulse});
    session.feedSpectrum(spectrum);
    if (time !== null) session.freezeTime(time);
    await frames();
    return report();
}

async function decode(url) {
    const bitmap = await createImageBitmap(await (await fetch(url)).blob(),
        {colorSpaceConversion: 'none', premultiplyAlpha: 'none'});
    const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
    const context = canvas.getContext('2d');
    context.drawImage(bitmap, 0, 0);
    return context.getImageData(0, 0, bitmap.width, bitmap.height);
}

function plane(image) {
    const data = new Float64Array(image.width * image.height);
    for (let index = 0; index < data.length; ++index) data[index] = image.data[4 * index];
    return {width: image.width, height: image.height, data};
}

// Runs the shared gate on the mounted, frozen editor. Each region is
// {name, box, omit}: the frame's pixels in the box are measured against the
// same frame drawn without the `omit` draw ids. `state` regions are measured
// against the frame with Bypass in its other state. `reference` is the URL
// of a greyscale native reference at CSS size; `fieldReference` one of the
// native field drawn alone, compared with this field drawn alone.
async function gate({regions, reference = null, fieldReference = null, bypassState = null}) {
    const ink = {};
    for (const {name, box, omit} of regions) {
        ink[name] = regionInk(session.snapshot({region: box}), session.snapshot({omit, region: box}));
    }
    if (bypassState) {
        const box = session.bounds[bypassState];
        const before = session.snapshot({region: box});
        const original = values.get(4);
        adapter.setParameterValue(4, original >= 0.5 ? 0 : 1);
        ink[`${bypassState}/state`] = regionInk(session.snapshot({region: box}), before);
        adapter.setParameterValue(4, original);
        // Draw the restored state now, so the last frame is the measured one.
        session.snapshot({region: box});
    }
    let similarity = null;
    let fieldSimilarity = null;
    let withoutField = null;
    if (reference) {
        const native = plane(await decode(reference));
        similarity = ssim(reduceLuma(session.snapshot()), native);
        withoutField = ssim(reduceLuma(session.snapshot({backdrop: false})), native);
    }
    if (fieldReference) {
        fieldSimilarity = ssim(reduceLuma(session.snapshot({nodes: false})),
            plane(await decode(fieldReference)));
    }
    const result = verdict({ink, similarity});
    const frame = session.stats.lastFrame;
    return {...result, fieldSimilarity, withoutField, time: frame.time, digest: frame.digest,
        commandCount: frame.commandCount, size: session.size};
}

async function chooseFile(name) {
    const input = container.querySelector('input[type=file]');
    if (!input) return null;
    const transfer = new DataTransfer();
    transfer.items.add(new File([new Uint8Array([82, 73, 70, 70])], name));
    input.files = transfer.files;
    input.dispatchEvent(new Event('change'));
    await frames();
    return report();
}

async function teardown() {
    const before = calls.length;
    session.destroy();
    await frames();
    return {
        canvasRemoved: container.querySelector('canvas') === null,
        pendingFrame: session.pendingFrame,
        paramsHandlerRestored: adapter.onParamsChanged === null,
        callsAfterTeardown: calls.length - before,
    };
}

Object.assign(globalThis.__editorFixture, {
    booted: true,
    mount,
    settle: async () => { await frames(); return report(); },
    chooseFile,
    capture: async () => session.capture(),
    gate,
    // Headless Chrome's emulated device scale reaches devicePixelRatio but not
    // a new element's device-pixel-content-box, so a canvas mounted under it
    // is measured once more through the session's window resize path.
    remeasure: async () => {
        dispatchEvent(new Event('resize'));
        await frames();
        return report();
    },
    // The digest of the frame at `time`, drawn now with the clock frozen.
    digestAt: async time => {
        session.freezeTime(time);
        return session.stats.lastFrame.digest;
    },
    // The current mode and the digest of the frame drawn now, clock frozen.
    frozenFrame: async () => {
        session.freezeTime(session.time);
        return {mode: view.mode, commandCount: session.stats.lastFrame.commandCount,
            digest: session.stats.lastFrame.digest};
    },
    teardown,
});
