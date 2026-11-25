// Mounts a knob and a label through the editor session against a recording
// stand-in for the web player's HostAdapter. The browser harness drives real
// input through CDP and reads the results through `__editorFixture`.
import createVellumCore from './vellum_web_core.js';
import {createEditorSession} from './session/editor_session.js';

const params = [{id: 0, label: 'Mix', type: 'float', minValue: 0, maxValue: 1, defaultValue: 0.5}];
const values = new Map([[0, 0.5]]);
const calls = [];
const adapter = {
    descriptor: {name: 'Editor session fixture', hasState: true},
    audioNode: null,
    onParamsChanged: null,
    onIrChanged: null,
    async getParameterInfo() { return params.map(info => ({...info})); },
    async getParameterValue(id) { return values.get(id); },
    setParameterValue(id, value) {
        calls.push({call: 'set', param: id, value});
        values.set(id, value);
        // Echo the write the way a plug-in reports a parameter change.
        this.onParamsChanged?.(params.map(info => values.get(info.id)), params);
    },
    beginGesture(id) { calls.push({call: 'begin', param: id}); },
    endGesture(id) { calls.push({call: 'end', param: id}); },
    async getState() { return new Uint8Array([Math.round(values.get(0) * 100)]); },
    async setState(bytes) { values.set(0, bytes[0] / 100); },
};

function layout(width, height) {
    return {
        title: {x: 16, y: 12, width: width - 32, height: 28},
        mix: {x: 16, y: 52, width: Math.round(width * 0.3), height: height - 68},
        spectrum: {
            x: Math.round(width * 0.3) + 32, y: 52,
            width: width - Math.round(width * 0.3) - 48, height: height - 68,
        },
    };
}

const view = {
    background: '#0b1020',
    layout,
    nodes: [
        {id: 'title', kind: 'label', text: 'Superconvolver', fontSize: 20},
        {id: 'mix', kind: 'knob', param: 0},
        {id: 'spectrum', kind: 'plot', feed: 'spectrum'},
    ],
};

const container = document.querySelector('#editor');
const frames = () => new Promise(resolve =>
    requestAnimationFrame(() => requestAnimationFrame(resolve)));
let session = null;

function snapshot() {
    const canvas = container.querySelector('canvas');
    const rect = canvas?.getBoundingClientRect();
    return {
        size: session.size,
        bounds: session.bounds,
        stats: session.stats,
        gestures: session.gestures,
        calls: calls.map(call => ({...call})),
        value: session.value(0),
        canvas: canvas ? {
            width: canvas.width, height: canvas.height,
            left: rect.left, top: rect.top, cssWidth: rect.width, cssHeight: rect.height,
        } : null,
    };
}

async function exerciseBridges() {
    const result = {};
    const before = session.stats;
    session.feedSpectrum(Array.from({length: 32}, (_, index) => (index % 8) / 8));
    await frames();
    result.spectrumRepainted = session.stats.frames > before.frames &&
        session.stats.lastFrame.commandCount > before.lastFrame.commandCount;
    adapter.onIrChanged({name: 'Hall.wav', samples: [1, 0.5, 0.25]});
    result.irName = session.impulseResponseName;
    const saved = await session.saveState();
    await session.loadState(new Uint8Array([25]));
    result.loadedValue = session.value(0);
    await session.loadState(saved);
    result.restoredValue = session.value(0);
    const chosen = session.selectFile({accept: '.wav'});
    const input = container.querySelector('input[type=file]');
    const transfer = new DataTransfer();
    transfer.items.add(new File([new Uint8Array([82, 73, 70, 70])], 'Room.wav'));
    input.files = transfer.files;
    input.dispatchEvent(new Event('change'));
    result.selectedFile = (await chosen)?.name ?? null;
    result.fileInputRemoved = container.querySelector('input[type=file]') === null;
    try {
        await createEditorSession({
            container: document.querySelector('#scratch'), adapter, createCore: createVellumCore,
            view: {...view, nodes: [{id: 'ghost', kind: 'knob', param: 9}]},
        });
        result.absentParameterRefused = false;
    } catch (error) {
        result.absentParameterRefused = /absent parameter 9/.test(String(error)) &&
            document.querySelector('#scratch canvas') === null;
    }
    return result;
}

async function teardown() {
    const pending = session.selectFile();
    const framesBefore = session.stats.frames;
    session.destroy();
    session.destroy();
    const fileRequestSettled = (await pending) === null;
    container.style.width = '300px';
    await frames();
    return {
        canvasRemoved: container.querySelector('canvas') === null,
        fileInputRemoved: container.querySelector('input') === null,
        fileRequestSettled,
        pendingFrame: session.pendingFrame,
        framesAfterResize: session.stats.frames - framesBefore,
        paramsHandlerRestored: adapter.onParamsChanged === null,
        irHandlerRestored: adapter.onIrChanged === null,
        callsAfterTeardown: calls.length,
    };
}

const ready = (async () => {
    session = await createEditorSession({container, adapter, view, createCore: createVellumCore});
    await frames();
})();

Object.assign(globalThis.__editorFixture, {
    booted: true,
    ready: () => ready.then(snapshot),
    snapshot: async () => snapshot(),
    settle: async () => { await frames(); return snapshot(); },
    resizeContainer: async (width, height) => {
        container.style.width = `${width}px`;
        container.style.height = `${height}px`;
        await frames();
        return snapshot();
    },
    exerciseBridges,
    capture: async () => session.capture(),
    teardown,
});
