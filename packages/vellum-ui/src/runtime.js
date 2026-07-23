const ELEMENT = Symbol.for('vellum.element');
const FRAGMENT = Symbol.for('vellum.fragment');
const PROTOCOL = 'vellum.authoring-host.v1';

let renderingRuntime = null;

function canonicalize(value) {
    if (value === null || typeof value !== 'object') return value;
    if (Array.isArray(value)) return value.map(canonicalize);
    return Object.fromEntries(
        Object.keys(value)
            .sort()
            .filter((key) => value[key] !== undefined)
            .map((key) => [key, canonicalize(value[key])]),
    );
}

function stableStringify(value) {
    return JSON.stringify(canonicalize(value));
}

function assertJsonValue(value, path = 'value', ancestors = new Set()) {
    if (value === null || ['string', 'boolean'].includes(typeof value)) return;
    if (typeof value === 'number') {
        if (!Number.isFinite(value)) throw new TypeError(`${path} must be finite`);
        return;
    }
    if (typeof value !== 'object') {
        throw new TypeError(`${path} is not JSON-serializable`);
    }
    if (ancestors.has(value)) throw new TypeError(`${path} contains a cycle`);
    ancestors.add(value);
    if (Array.isArray(value)) {
        value.forEach((item, index) => assertJsonValue(item, `${path}[${index}]`, ancestors));
    } else {
        const prototype = Object.getPrototypeOf(value);
        if (prototype !== Object.prototype && prototype !== null) {
            throw new TypeError(`${path} must be a plain object`);
        }
        for (const [key, item] of Object.entries(value)) {
            assertJsonValue(item, `${path}.${key}`, ancestors);
        }
    }
    ancestors.delete(value);
}

function cloneJson(value, path = 'value') {
    assertJsonValue(value, path);
    return JSON.parse(stableStringify(value));
}

function flattenChildren(value, output = []) {
    if (Array.isArray(value)) {
        for (const child of value) flattenChildren(child, output);
    } else if (value !== null && value !== undefined && value !== false && value !== true) {
        output.push(value);
    }
    return output;
}

export function jsx(type, properties = {}, key) {
    const props = properties == null ? {} : { ...properties };
    if (key !== undefined) props.key = key;
    return Object.freeze({
        $$typeof: ELEMENT,
        type,
        props: Object.freeze(props),
    });
}

export const jsxs = jsx;
export const Fragment = FRAGMENT;

function primitive(type) {
    return function VellumPrimitive(properties = {}) {
        return jsx(type, properties);
    };
}

export const View = primitive('view');
export const Stack = primitive('stack');
export const Text = primitive('text');
export const Button = primitive('button');
export const Image = primitive('image');
export const Canvas = primitive('canvas');

function assertRuntime(hook) {
    if (renderingRuntime === null) {
        throw new Error(`${hook} must be called while rendering a mounted Vellum component`);
    }
    return renderingRuntime;
}

export function useState(initialValue) {
    const runtime = assertRuntime('useState');
    const index = runtime.hookCursor++;
    if (index >= runtime.hooks.length) {
        if (runtime.hasRendered) {
            throw new Error('hook order changed between renders');
        }
        runtime.hooks.push({
            kind: 'state',
            value: typeof initialValue === 'function' ? initialValue() : initialValue,
        });
    }
    const record = runtime.hooks[index];
    if (!record || record.kind !== 'state') {
        throw new Error('hook kind changed between renders');
    }
    const setValue = (nextValue) => {
        const previous = record.value;
        record.value = typeof nextValue === 'function'
            ? nextValue(previous)
            : nextValue;
        runtime.dirty = true;
    };
    return [record.value, setValue];
}

export function useMemo(factory, dependencies) {
    const runtime = assertRuntime('useMemo');
    const index = runtime.hookCursor++;
    let prior = runtime.hooks[index];
    if (!prior && runtime.hasRendered) {
        throw new Error('hook order changed between renders');
    }
    if (prior && prior.kind !== 'memo') {
        throw new Error('hook kind changed between renders');
    }
    const nextDependencies = Array.isArray(dependencies) ? [...dependencies] : null;
    const unchanged = prior && nextDependencies && prior.dependencies &&
        prior.dependencies.length === nextDependencies.length &&
        prior.dependencies.every((value, item) => Object.is(value, nextDependencies[item]));
    if (!unchanged) {
        runtime.hooks[index] = {
            kind: 'memo',
            value: factory(),
            dependencies: nextDependencies,
        };
        prior = runtime.hooks[index];
    }
    return prior.value;
}

function validateStyle(style, path) {
    if (style === undefined) return undefined;
    if (style === null || typeof style !== 'object' || Array.isArray(style)) {
        throw new TypeError(`${path}.style must be an object`);
    }
    const result = {};
    for (const [name, value] of Object.entries(style)) {
        if (!['string', 'number', 'boolean'].includes(typeof value)) {
            throw new TypeError(`${path}.style.${name} is not serializable`);
        }
        if (typeof value === 'number' && !Number.isFinite(value)) {
            throw new TypeError(`${path}.style.${name} must be finite`);
        }
        result[name] = value;
    }
    return result;
}

function textNode(value, path) {
    return {
        type: 'text-run',
        id: `${path}/text`,
        text: String(value),
        children: [],
    };
}

function materialize(value, runtime, path) {
    if (typeof value === 'string' || typeof value === 'number') {
        return [textNode(value, path)];
    }
    if (!value || value.$$typeof !== ELEMENT) {
        throw new TypeError(`${path} is not a Vellum element`);
    }

    if (value.type === FRAGMENT) {
        return flattenChildren(value.props.children).flatMap((child, index) =>
            materialize(child, runtime, `${path}/${index}`));
    }
    if (typeof value.type === 'function') {
        return materialize(value.type(value.props), runtime, path);
    }
    if (typeof value.type !== 'string') {
        throw new TypeError(`${path}.type must be a component or intrinsic string`);
    }

    const children = flattenChildren(value.props.children).flatMap((child, index) =>
        materialize(child, runtime, `${path}/${index}`));
    const id = typeof value.props.id === 'string' && value.props.id.length > 0
        ? value.props.id
        : `${path}/${value.type}`;
    if (runtime.nodeIds.has(id)) {
        throw new Error(`duplicate Vellum node id: ${id}`);
    }
    runtime.nodeIds.add(id);
    const events = {};
    for (const [property, eventName] of [
        ['onPress', 'press'],
        ['onChange', 'change'],
        ['onSubmit', 'submit'],
        ['onKeyDown', 'keyDown'],
    ]) {
        const handler = value.props[property];
        if (handler === undefined) continue;
        if (typeof value.props.id !== 'string' || value.props.id.length === 0) {
            throw new Error(`${path}.${property} requires an explicit stable id`);
        }
        if (typeof handler === 'function') {
            const action = `${id}:${eventName}`;
            if (runtime.namedActions.has(action)) {
                throw new Error(`inline Vellum action collides with named action: ${action}`);
            }
            runtime.handlers.set(action, handler);
            events[eventName] = action;
        } else if (typeof handler === 'string' && handler.length > 0) {
            events[eventName] = handler;
        } else {
            throw new TypeError(`${path}.${property} must be a function or action name`);
        }
    }

    const node = {
        type: value.type,
        id,
        style: validateStyle(value.props.style, path),
        text: typeof value.props.text === 'string' ? value.props.text : undefined,
        source: typeof value.props.source === 'string' ? value.props.source : undefined,
        accessibilityLabel: typeof value.props.accessibilityLabel === 'string'
            ? value.props.accessibilityLabel
            : undefined,
        events: Object.keys(events).length > 0 ? events : undefined,
        children,
    };
    return [node];
}

class Runtime {
    constructor(render, options = {}) {
        if (typeof render !== 'function') throw new TypeError('createApp requires a render function');
        this.renderFunction = render;
        this.hooks = [];
        this.hookCursor = 0;
        this.handlers = new Map();
        this.nodeIds = new Set();
        this.namedActions = new Map(Object.entries(options.actions ?? {}));
        this.model = options.initialState ?? null;
        this.dirty = true;
        this.lastTree = null;
        this.hasRendered = false;
    }

    render() {
        this.hookCursor = 0;
        this.handlers = new Map();
        this.nodeIds = new Set();
        const prior = renderingRuntime;
        renderingRuntime = this;
        try {
            const root = this.renderFunction(this.model);
            const materialized = materialize(root, this, 'root');
            if (materialized.length !== 1) {
                throw new Error('a Vellum application must render exactly one root element');
            }
            if (this.hookCursor !== this.hooks.length) {
                throw new Error('hook order changed between renders');
            }
            this.lastTree = materialized[0];
            this.dirty = false;
            this.hasRendered = true;
            return this.lastTree;
        } finally {
            renderingRuntime = prior;
        }
    }

    dispatch(action, payload) {
        const inline = this.handlers.get(action);
        if (inline) {
            inline(payload);
        } else {
            const named = this.namedActions.get(action);
            if (!named) throw new Error(`unknown Vellum action: ${action}`);
            const nextModel = named(this.model, payload);
            if (nextModel !== undefined) this.model = nextModel;
            this.dirty = true;
        }
        return this.render();
    }

    snapshot() {
        return cloneJson({ hooks: this.hooks, model: this.model }, 'state');
    }

    restore(snapshot) {
        if (!snapshot || typeof snapshot !== 'object' || Array.isArray(snapshot)) {
            throw new TypeError('Vellum state snapshot must be an object');
        }
        if (!Array.isArray(snapshot.hooks)) {
            throw new TypeError('Vellum state snapshot hooks must be an array');
        }
        const restored = cloneJson(snapshot, 'state');
        this.hooks = restored.hooks;
        this.model = restored.model ?? null;
        this.dirty = true;
    }
}

export function createApp(options) {
    if (typeof options === 'function') return new Runtime(options);
    if (!options || typeof options !== 'object') {
        throw new TypeError('createApp expects a render function or options object');
    }
    return new Runtime(options.render, options);
}

export function mount(application) {
    const runtime = application instanceof Runtime ? application : createApp(application);
    const bridge = Object.freeze({
        protocol: PROTOCOL,
        renderJSON() {
            return stableStringify({ protocol: PROTOCOL, tree: runtime.render() });
        },
        dispatchJSON(requestJSON) {
            const request = JSON.parse(requestJSON);
            if (!request || request.protocol !== PROTOCOL || typeof request.action !== 'string') {
                throw new Error(`invalid ${PROTOCOL} dispatch request`);
            }
            return stableStringify({
                protocol: PROTOCOL,
                tree: runtime.dispatch(request.action, request.payload ?? null),
            });
        },
        snapshotStateJSON() {
            return stableStringify({ protocol: PROTOCOL, state: runtime.snapshot() });
        },
        restoreStateJSON(snapshotJSON) {
            const envelope = JSON.parse(snapshotJSON);
            if (!envelope || envelope.protocol !== PROTOCOL) {
                throw new Error(`invalid ${PROTOCOL} state snapshot`);
            }
            runtime.restore(envelope.state);
            return stableStringify({ protocol: PROTOCOL, tree: runtime.render() });
        },
    });
    Object.defineProperty(globalThis, '__vellum', {
        configurable: true,
        enumerable: false,
        writable: false,
        value: bridge,
    });
    return bridge;
}
