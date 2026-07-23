const ELEMENT = Symbol.for('vellum.element');
const FRAGMENT = Symbol.for('vellum.fragment');
const PROTOCOL = 'vellum.authoring-host.v1';
const SNAPSHOT_SCHEMA = 'vellum.authoring-state.v1';
const TEXT_INPUT_PRIMITIVE_VERSION = 1;
const MAXIMUM_TEXT_INPUT_LENGTH = 65536;
const MAXIMUM_TEXT_INPUT_PLACEHOLDER_LENGTH = 1024;
const INTRINSIC_TYPES = new Set([
    'view', 'stack', 'text', 'text-input', 'button', 'image', 'canvas', 'custom',
]);

let renderingRuntime = null;

function assertPlainJson(value, path = 'value', ancestors = new Set()) {
    if (value === null || ['string', 'boolean'].includes(typeof value)) return;
    if (typeof value === 'number') {
        if (!Number.isFinite(value)) throw new TypeError(`${path} must be finite`);
        return;
    }
    if (typeof value !== 'object') throw new TypeError(`${path} is not JSON-serializable`);
    if (ancestors.has(value)) throw new TypeError(`${path} contains a cycle`);
    const symbols = Object.getOwnPropertySymbols(value);
    if (symbols.length > 0) throw new TypeError(`${path} contains symbol properties`);
    ancestors.add(value);
    if (Array.isArray(value)) {
        const names = Object.getOwnPropertyNames(value);
        const indexes = names.filter((name) => name !== 'length');
        if (indexes.length !== value.length ||
            indexes.some((name, index) => name !== String(index))) {
            throw new TypeError(`${path} must be a dense array without extra properties`);
        }
        for (const name of names) {
            if (name === 'length') continue;
            const descriptor = Object.getOwnPropertyDescriptor(value, name);
            if (!descriptor?.enumerable || descriptor.get || descriptor.set) {
                throw new TypeError(`${path}[${name}] has unsupported property semantics`);
            }
        }
        value.forEach((item, index) => assertPlainJson(item, `${path}[${index}]`, ancestors));
    } else {
        const prototype = Object.getPrototypeOf(value);
        if (prototype !== Object.prototype && prototype !== null) {
            throw new TypeError(`${path} must be a plain object`);
        }
        for (const name of Object.getOwnPropertyNames(value)) {
            const descriptor = Object.getOwnPropertyDescriptor(value, name);
            if (!descriptor?.enumerable || descriptor.get || descriptor.set) {
                throw new TypeError(`${path}.${name} has unsupported property semantics`);
            }
            assertPlainJson(descriptor.value, `${path}.${name}`, ancestors);
        }
    }
    ancestors.delete(value);
}

function canonicalize(value) {
    if (value === null || typeof value !== 'object') return value;
    if (Array.isArray(value)) return value.map(canonicalize);
    const output = Object.create(null);
    for (const key of Object.keys(value).sort()) output[key] = canonicalize(value[key]);
    return output;
}

function stableStringify(value) {
    assertPlainJson(value);
    return JSON.stringify(canonicalize(value));
}

function cloneJson(value, path = 'value') {
    assertPlainJson(value, path);
    return JSON.parse(stableStringify(value));
}

function freezeJson(value) {
    if (value && typeof value === 'object') {
        for (const item of Object.values(value)) freezeJson(item);
        Object.freeze(value);
    }
    return value;
}

function durableValue(value, path) {
    return freezeJson(cloneJson(value, path));
}

function fnv1a32(value) {
    let hash = 0x811c9dc5;
    for (let index = 0; index < value.length; index += 1) {
        hash ^= value.charCodeAt(index);
        hash = Math.imul(hash, 0x01000193);
    }
    return (hash >>> 0).toString(36);
}

function flattenChildren(value, output = []) {
    if (Array.isArray(value)) {
        for (const child of value) flattenChildren(child, output);
    } else if (value !== null && value !== undefined && value !== false && value !== true) {
        output.push(value);
    }
    return output;
}

function escapedIdentity(value) {
    const text = String(value);
    let encoded = `${text.length}:`;
    for (let index = 0; index < text.length; index += 1) {
        encoded += text.charCodeAt(index).toString(16).padStart(4, '0');
    }
    return encoded;
}

function elementPath(parent, child, index) {
    if (child && child.$$typeof === ELEMENT) {
        if (child.props.key !== undefined) return `${parent}/key:${escapedIdentity(child.props.key)}`;
        if (typeof child.props.id === 'string' && child.props.id.length > 0) {
            return `${parent}/id:${escapedIdentity(child.props.id)}`;
        }
    }
    return `${parent}/index:${index}`;
}

export function jsx(type, properties = {}, key) {
    const props = properties == null ? {} : { ...properties };
    if (key !== undefined) props.key = key;
    return Object.freeze({ $$typeof: ELEMENT, type, props: Object.freeze(props) });
}

export const jsxs = jsx;
export const Fragment = FRAGMENT;

function primitive(type) {
    const component = function VellumPrimitive(properties = {}) {
        return jsx(type, properties);
    };
    component.displayName = `Vellum.${type}`;
    return component;
}

export const View = primitive('view');
export const Stack = primitive('stack');
export const Text = primitive('text');
export const Button = primitive('button');
export const Image = primitive('image');
export const Canvas = primitive('canvas');
export function TextInput(properties = {}) {
    return jsx('text-input', { ...properties, primitiveVersion: TEXT_INPUT_PRIMITIVE_VERSION });
}
TextInput.displayName = 'Vellum.TextInput';

export function CustomComponent({ component, properties = {}, fallback, children, ...rest } = {}) {
    if (typeof component !== 'string' || !/^[a-z][a-z0-9-]{0,63}$/.test(component)) {
        throw new TypeError('CustomComponent component must be a lowercase declared identifier');
    }
    if (fallback !== undefined && children !== undefined) {
        throw new TypeError('CustomComponent accepts fallback or children, not both');
    }
    return jsx('custom', {
        ...rest,
        component,
        properties: durableValue(properties, `CustomComponent(${component}).properties`),
        children: fallback ?? children ?? null,
    });
}

function designToken(document, value) {
    if (typeof value !== 'string' || !value.startsWith('{') || !value.endsWith('}')) return value;
    const reference = value.slice(1, -1);
    const namespace = document?.source?.namespace;
    const token = document?.tokens?.[reference] ??
        (namespace ? document?.tokens?.[`${namespace}.${reference}`] : undefined);
    return token?.$value ?? value;
}

function designStyle(document, node, root) {
    const properties = node.properties ?? {};
    const layout = properties.layout ?? {};
    const paint = properties.paint ?? {};
    const text = properties.text ?? {};
    const style = {};
    for (const property of ['x', 'y', 'width', 'height', 'padding', 'gap']) {
        if (typeof layout[property] === 'number' && Number.isFinite(layout[property])) {
            style[property] = layout[property];
        }
    }
    if (root) {
        style.width ??= 800;
        style.height ??= 600;
        style.padding ??= 0;
    }
    if (layout.direction === 'row') style.direction = 'horizontal';
    else if (layout.direction === 'column') style.direction = 'vertical';
    if (typeof paint.backgroundColor === 'string') {
        style.backgroundColor = designToken(document, paint.backgroundColor);
    }
    if (typeof paint.borderRadius === 'number') style.borderRadius = paint.borderRadius;
    if (typeof text.fontSize === 'number') style.fontSize = text.fontSize;
    if (typeof text.color === 'string') style.color = designToken(document, text.color);
    if (node.kind === 'button') {
        style.width ??= 180;
        style.height ??= 48;
    }
    return style;
}

const DESIGN_EVENT_PROPERTIES = Object.freeze({
    press: 'onPress',
    change: 'onChange',
    submit: 'onSubmit',
    keyDown: 'onKeyDown',
});

function resolvedDesignActions(document, bindings, actions) {
    if (bindings === null || bindings === undefined) return new Map();
    if (typeof bindings !== 'object' || Array.isArray(bindings) ||
        !Array.isArray(bindings.bindings)) {
        throw new TypeError('Design bindings require a generated binding document');
    }
    if (bindings.schema !== 'vellum.generated-bindings.v1') {
        throw new Error(`unsupported Design binding schema: ${String(bindings.schema)}`);
    }
    const sourceKey = document.source?.key ?? document.source?.namespace;
    if (typeof sourceKey !== 'string' || sourceKey.length === 0 ||
        bindings.sourceKey !== sourceKey) {
        throw new Error('Design binding source does not match imported design');
    }
    if (typeof document.source?.revision !== 'string' ||
        document.source.revision.length === 0 ||
        bindings.revision !== document.source.revision) {
        throw new Error('Design binding revision does not match imported design');
    }
    const resolved = new Map();
    for (const [index, binding] of bindings.bindings.entries()) {
        if (!binding || typeof binding !== 'object' || Array.isArray(binding) ||
            typeof binding.resolvedNodeId !== 'string' ||
            binding.resolvedNodeId.length === 0 ||
            typeof binding.action !== 'string' || binding.action.length === 0 ||
            typeof binding.event !== 'string') {
            throw new TypeError(`Design binding ${index} is invalid`);
        }
        const property = DESIGN_EVENT_PROPERTIES[binding.event];
        if (!property) {
            throw new Error(
                `Design binding ${index} uses unsupported event '${binding.event}'`,
            );
        }
        if (!Object.hasOwn(actions, binding.action)) {
            throw new Error(
                `Design binding '${binding.action}' has no developer-owned action`,
            );
        }
        const handler = actions[binding.action];
        if (typeof handler !== 'function' &&
            !(typeof handler === 'string' && handler.length > 0)) {
            throw new TypeError(
                `Design action '${binding.action}' must be a function or action name`,
            );
        }
        const byEvent = resolved.get(binding.resolvedNodeId) ?? new Map();
        if (byEvent.has(property)) {
            throw new Error(
                `duplicate Design binding for ${binding.resolvedNodeId}.${binding.event}`,
            );
        }
        byEvent.set(property, handler);
        resolved.set(binding.resolvedNodeId, byEvent);
    }
    return resolved;
}

function designElement(
    document, node, actions, bindings, visitedBindings, legacyActions, root = false,
) {
    if (!node || typeof node !== 'object' || typeof node.id !== 'string' ||
        !Array.isArray(node.children)) {
        throw new TypeError('Design requires normalized DesignIR nodes');
    }
    const type = node.kind === 'button' ? 'button'
        : node.kind === 'text' ? 'text'
            : node.properties?.layout?.display === 'flex' || root ? 'stack' : 'view';
    const properties = {
        id: node.id,
        style: designStyle(document, node, root),
        children: node.kind === 'text' || node.kind === 'button'
            ? node.text ?? node.name ?? ''
            : node.children.map((child) =>
                designElement(
                    document, child, actions, bindings, visitedBindings, legacyActions,
                )),
    };
    if (legacyActions) {
        const action = actions?.[node.id];
        if (action !== undefined) properties.onPress = action;
    }
    const generated = bindings.get(node.id);
    if (generated !== undefined) {
        visitedBindings.add(node.id);
        for (const [property, handler] of generated) {
            if (properties[property] !== undefined) {
                throw new Error(`Design action conflicts with generated binding for ${node.id}`);
            }
            properties[property] = handler;
        }
    }
    return jsx(type, properties);
}

export function Design({ document, actions = {}, bindings = null }) {
    if (!document || typeof document !== 'object' || !document.root ||
        typeof actions !== 'object' || actions === null || Array.isArray(actions)) {
        throw new TypeError('Design requires a normalized document and an action map');
    }
    const resolvedBindings = resolvedDesignActions(document, bindings, actions);
    const visitedBindings = new Set();
    const result = designElement(
        document, document.root, actions, resolvedBindings, visitedBindings,
        bindings === null || bindings === undefined, true,
    );
    for (const nodeId of resolvedBindings.keys()) {
        if (!visitedBindings.has(nodeId)) {
            throw new Error(`Design binding target is missing from imported design: ${nodeId}`);
        }
    }
    return result;
}

function activeHook(kind) {
    if (renderingRuntime === null || renderingRuntime.renderState === null ||
        renderingRuntime.renderState.frameStack.length === 0) {
        throw new Error(`${kind} must be called while rendering a mounted Vellum component`);
    }
    const runtime = renderingRuntime;
    const state = runtime.renderState;
    const frameId = state.frameStack[state.frameStack.length - 1];
    const frame = state.frames.get(frameId);
    const index = frame.cursor++;
    return { runtime, state, frameId, frame, index };
}

export function useState(initialValue) {
    const { runtime, frameId, frame, index } = activeHook('useState');
    if (index >= frame.hooks.length) {
        if (frame.established) throw new Error(`hook order changed in ${frameId}`);
        const initial = typeof initialValue === 'function' ? initialValue() : initialValue;
        frame.hooks.push({ kind: 'state', value: durableValue(initial, `${frameId}[${index}]`) });
    }
    const record = frame.hooks[index];
    if (record.kind !== 'state') throw new Error(`hook kind changed in ${frameId}`);
    const setValue = (nextValue) => {
        if (runtime.renderState !== null) throw new Error('state cannot change during render');
        const frames = runtime.mutationFrames ?? runtime.frames;
        const target = frames.get(frameId)?.hooks[index];
        if (!target || target.kind !== 'state') throw new Error('state hook is no longer mounted');
        const next = typeof nextValue === 'function' ? nextValue(target.value) : nextValue;
        target.value = durableValue(next, `${frameId}[${index}]`);
        runtime.dirty = true;
    };
    return [record.value, setValue];
}

export function useMemo(factory, dependencies) {
    const { frameId, frame, index } = activeHook('useMemo');
    const nextDependencies = Array.isArray(dependencies) ? [...dependencies] : null;
    if (index >= frame.hooks.length) {
        if (frame.established) throw new Error(`hook order changed in ${frameId}`);
        frame.hooks.push({ kind: 'memo', initialized: false, value: undefined, dependencies: null });
    }
    const record = frame.hooks[index];
    if (record.kind !== 'memo') throw new Error(`hook kind changed in ${frameId}`);
    const unchanged = record.initialized && nextDependencies && record.dependencies &&
        record.dependencies.length === nextDependencies.length &&
        record.dependencies.every((value, item) => Object.is(value, nextDependencies[item]));
    if (!unchanged) {
        record.value = factory();
        record.dependencies = nextDependencies;
        record.initialized = true;
    }
    return record.value;
}

function validateStyle(style, path) {
    if (style === undefined) return undefined;
    if (style === null || typeof style !== 'object' || Array.isArray(style)) {
        throw new TypeError(`${path}.style must be an object`);
    }
    const prototype = Object.getPrototypeOf(style);
    if (prototype !== Object.prototype && prototype !== null) {
        throw new TypeError(`${path}.style must be a plain object`);
    }
    if (Object.getOwnPropertySymbols(style).length > 0) {
        throw new TypeError(`${path}.style contains symbol properties`);
    }
    const result = Object.create(null);
    for (const name of Object.getOwnPropertyNames(style)) {
        const descriptor = Object.getOwnPropertyDescriptor(style, name);
        if (!descriptor?.enumerable || descriptor.get || descriptor.set) {
            throw new TypeError(`${path}.style.${name} has unsupported property semantics`);
        }
        const value = descriptor.value;
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

function textNode(value, runtime, path) {
    const id = `${path}/text`;
    if (runtime.renderState.nodeIds.has(id)) throw new Error(`duplicate Vellum node id: ${id}`);
    runtime.renderState.nodeIds.add(id);
    return { type: 'text-run', id, text: String(value), children: [] };
}

function materialize(value, runtime, path) {
    if (typeof value === 'string' || typeof value === 'number') {
        return [textNode(value, runtime, path)];
    }
    if (!value || value.$$typeof !== ELEMENT) throw new TypeError(`${path} is not a Vellum element`);
    if (value.type === FRAGMENT) {
        return flattenChildren(value.props.children).flatMap((child, index) =>
            materialize(child, runtime, elementPath(path, child, index)));
    }
    if (typeof value.type === 'function') {
        const componentIdentity = runtime.componentIdentity(value.type);
        const frameId = `${path}/component:${escapedIdentity(componentIdentity)}`;
        if (runtime.renderState.visitedFrames.has(frameId) &&
            typeof value.props.id === 'string' && runtime.renderState.nodeIds.has(value.props.id)) {
            throw new Error(`duplicate Vellum node id: ${value.props.id}`);
        }
        const ownedPath = `${path}/owner:${escapedIdentity(componentIdentity)}`;
        return runtime.withFrame(
            frameId,
            () => materialize(value.type(value.props), runtime, ownedPath),
        );
    }
    if (typeof value.type !== 'string') {
        throw new TypeError(`${path}.type must be a component or intrinsic string`);
    }
    if (!INTRINSIC_TYPES.has(value.type)) {
        throw new TypeError(`${path}.type is not a supported Vellum intrinsic`);
    }

    const childValues = flattenChildren(value.props.children);
    if (value.type === 'text-input') {
        if (value.props.primitiveVersion !== TEXT_INPUT_PRIMITIVE_VERSION) {
            throw new Error(`${path} uses an unsupported TextInput primitive version`);
        }
        if (typeof value.props.id !== 'string' || value.props.id.length === 0) {
            throw new Error(`${path}.TextInput requires an explicit stable id`);
        }
        if (typeof value.props.value !== 'string' ||
            value.props.value.length > MAXIMUM_TEXT_INPUT_LENGTH) {
            throw new TypeError(
                `${path}.TextInput value must be a string of at most ` +
                `${MAXIMUM_TEXT_INPUT_LENGTH} code units`,
            );
        }
        if (value.props.placeholder !== undefined &&
            (typeof value.props.placeholder !== 'string' ||
             value.props.placeholder.length > MAXIMUM_TEXT_INPUT_PLACEHOLDER_LENGTH)) {
            throw new TypeError(
                `${path}.TextInput placeholder must be a string of at most ` +
                `${MAXIMUM_TEXT_INPUT_PLACEHOLDER_LENGTH} code units`,
            );
        }
        if (value.props.onChange === undefined) {
            throw new Error(`${path}.TextInput requires onChange for controlled state`);
        }
        if (childValues.length > 0) {
            throw new Error(`${path}.TextInput does not accept children`);
        }
    }
    const children = childValues.flatMap((child, index) =>
        materialize(child, runtime, elementPath(path, child, index)));
    const id = typeof value.props.id === 'string' && value.props.id.length > 0
        ? value.props.id : `${path}/${value.type}`;
    const state = runtime.renderState;
    if (state.nodeIds.has(id)) throw new Error(`duplicate Vellum node id: ${id}`);
    state.nodeIds.add(id);
    const events = {};
    for (const [property, eventName] of [
        ['onPress', 'press'], ['onChange', 'change'],
        ['onSubmit', 'submit'], ['onKeyDown', 'keyDown'],
    ]) {
        const handler = value.props[property];
        if (handler === undefined) continue;
        if (typeof value.props.id !== 'string' || value.props.id.length === 0) {
            throw new Error(`${path}.${property} requires an explicit stable id`);
        }
        if (typeof handler === 'function') {
            const action = `inline:${escapedIdentity(id)}:${eventName}`;
            state.handlers.set(action, handler);
            events[eventName] = action;
        } else if (typeof handler === 'string' && handler.length > 0) {
            if (!runtime.namedActions.has(handler)) {
                throw new Error(`unknown named Vellum action: ${handler}`);
            }
            events[eventName] = `named:${handler}`;
        } else {
            throw new TypeError(`${path}.${property} must be a function or action name`);
        }
    }
    const node = {
        type: value.type,
        id,
        children,
    };
    const style = validateStyle(value.props.style, path);
    if (style !== undefined) node.style = style;
    if (typeof value.props.text === 'string') node.text = value.props.text;
    if (typeof value.props.source === 'string') node.source = value.props.source;
    if (value.type === 'custom') {
        if (typeof value.props.component !== 'string' ||
            !/^[a-z][a-z0-9-]{0,63}$/.test(value.props.component)) {
            throw new TypeError(`${path}.component must be a lowercase declared identifier`);
        }
        if (value.props.properties === null || typeof value.props.properties !== 'object' ||
            Array.isArray(value.props.properties)) {
            throw new TypeError(`${path}.properties must be a plain JSON object`);
        }
        node.component = value.props.component;
        node.properties = cloneJson(value.props.properties, `${path}.properties`);
    }
    if (typeof value.props.accessibilityLabel === 'string') {
        node.accessibilityLabel = value.props.accessibilityLabel;
    }
    if (value.type === 'text-input') {
        node.primitiveVersion = TEXT_INPUT_PRIMITIVE_VERSION;
        node.value = value.props.value;
        if (value.props.placeholder !== undefined) node.placeholder = value.props.placeholder;
    }
    if (Object.keys(events).length > 0) node.events = events;
    return [node];
}

function cloneFrames(frames, resetMemo = false) {
    const output = new Map();
    for (const [id, frame] of frames) {
        output.set(id, {
            established: frame.established,
            cursor: 0,
            hooks: frame.hooks.map((hook) => hook.kind === 'state'
                ? { kind: 'state', value: durableValue(hook.value, `${id}.state`) }
                : {
                    kind: 'memo',
                    initialized: resetMemo ? false : hook.initialized,
                    value: resetMemo ? undefined : hook.value,
                    dependencies: resetMemo ? null : hook.dependencies,
                }),
        });
    }
    return output;
}

function frameFingerprint(frames) {
    const shape = [...frames.entries()]
        .sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0)
        .map(([id, frame]) => ({ id, hooks: frame.hooks.map((hook) => hook.kind) }));
    return fnv1a32(stableStringify(shape));
}

class Runtime {
    constructor(render, options = {}) {
        if (typeof render !== 'function') throw new TypeError('createApp requires a render function');
        this.renderFunction = render;
        this.applicationId = options.id || render.name || 'anonymous-vellum-app';
        if (typeof this.applicationId !== 'string' || this.applicationId.length === 0) {
            throw new TypeError('Vellum application id must be a non-empty string');
        }
        this.applicationStateVersion = options.stateVersion ?? '1';
        if (typeof this.applicationStateVersion !== 'string' ||
            this.applicationStateVersion.length === 0) {
            throw new TypeError('Vellum application stateVersion must be a non-empty string');
        }
        this.frames = new Map();
        this.handlers = new Map();
        this.namedActions = new Map(Object.entries(options.actions ?? {}));
        for (const [name, action] of this.namedActions) {
            if (!name || typeof action !== 'function') {
                throw new TypeError('Vellum named actions must map non-empty names to functions');
            }
        }
        this.model = durableValue(options.initialState ?? null, 'initialState');
        this.renderState = null;
        this.mutationFrames = null;
        this.dirty = true;
        this.lastTree = null;
    }

    componentIdentity(component) {
        const explicit = component.vellumId;
        const name = explicit ?? component.displayName ?? component.name ?? 'Anonymous';
        if (typeof name !== 'string' || name.length === 0) {
            throw new TypeError('Vellum component identity must be a non-empty string');
        }
        const identity = explicit === undefined
            ? `${name}@${fnv1a32(Function.prototype.toString.call(component))}`
            : `explicit:${name}`;
        const prior = this.renderState?.componentTypes.get(identity);
        if (prior !== undefined && prior !== component) {
            throw new Error(
                `ambiguous Vellum component identity: ${name}; assign distinct vellumId values`,
            );
        }
        this.renderState?.componentTypes.set(identity, component);
        return identity;
    }

    withFrame(id, callback) {
        const state = this.renderState;
        let frame = state.frames.get(id);
        if (!frame) {
            frame = { established: false, cursor: 0, hooks: [] };
            state.frames.set(id, frame);
        }
        if (state.visitedFrames.has(id)) throw new Error(`component frame rendered twice: ${id}`);
        state.visitedFrames.add(id);
        frame.cursor = 0;
        state.frameStack.push(id);
        try {
            const result = callback();
            if (frame.cursor !== frame.hooks.length) throw new Error(`hook order changed in ${id}`);
            frame.established = true;
            return result;
        } finally {
            state.frameStack.pop();
        }
    }

    renderCandidate(baseFrames, model) {
        const state = {
            frames: cloneFrames(baseFrames),
            handlers: new Map(),
            nodeIds: new Set(),
            visitedFrames: new Set(),
            componentTypes: new Map(),
            frameStack: [],
        };
        const priorRuntime = renderingRuntime;
        this.renderState = state;
        renderingRuntime = this;
        try {
            const rootName = this.renderFunction.name || 'Application';
            const root = this.withFrame(`root/component:${escapedIdentity(rootName)}`, () =>
                this.renderFunction(model));
            const materialized = materialize(root, this, 'root');
            if (materialized.length !== 1) {
                throw new Error('a Vellum application must render exactly one root element');
            }
            for (const id of [...state.frames.keys()]) {
                if (!state.visitedFrames.has(id)) state.frames.delete(id);
            }
            return { frames: state.frames, handlers: state.handlers, tree: materialized[0] };
        } finally {
            this.renderState = null;
            renderingRuntime = priorRuntime;
        }
    }

    commitRender(baseFrames = this.frames, model = this.model) {
        const candidate = this.renderCandidate(baseFrames, model);
        this.frames = candidate.frames;
        this.handlers = candidate.handlers;
        this.model = model;
        this.lastTree = candidate.tree;
        this.dirty = false;
        return this.lastTree;
    }

    render() {
        if (this.mutationFrames !== null || this.renderState !== null) {
            throw new Error('reentrant Vellum host operations are not supported');
        }
        return this.commitRender();
    }

    dispatch(action, payload) {
        if (this.mutationFrames !== null || this.renderState !== null) {
            throw new Error('reentrant Vellum host operations are not supported');
        }
        const workingFrames = cloneFrames(this.frames);
        let workingModel = this.model;
        this.mutationFrames = workingFrames;
        try {
            if (action.startsWith('inline:')) {
                const handler = this.handlers.get(action);
                if (!handler) throw new Error(`unknown Vellum action: ${action}`);
                handler(payload);
            } else if (action.startsWith('named:')) {
                const name = action.slice('named:'.length);
                const named = this.namedActions.get(name);
                if (!named) throw new Error(`unknown Vellum action: ${action}`);
                const nextModel = named(workingModel, payload);
                if (nextModel !== undefined) workingModel = durableValue(nextModel, 'model');
            } else {
                throw new Error(`invalid Vellum action namespace: ${action}`);
            }
            return this.commitRender(workingFrames, workingModel);
        } finally {
            this.mutationFrames = null;
        }
    }

    snapshot() {
        if (this.mutationFrames !== null || this.renderState !== null) {
            throw new Error('reentrant Vellum host operations are not supported');
        }
        if (this.lastTree === null) this.render();
        const stateFrames = [...this.frames.entries()]
            .sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0)
            .map(([id, frame]) => ({
                id,
                hooks: frame.hooks.map((hook) => hook.kind),
                values: frame.hooks
                    .map((hook, slot) => ({ hook, slot }))
                    .filter(({ hook }) => hook.kind === 'state')
                    .map(({ hook, slot }) => ({ slot, value: hook.value })),
            }));
        return {
            schema_version: SNAPSHOT_SCHEMA,
            application_id: this.applicationId,
            application_state_version: this.applicationStateVersion,
            layout_fingerprint: frameFingerprint(this.frames),
            model: this.model,
            frames: stateFrames,
        };
    }

    restore(snapshot) {
        if (this.mutationFrames !== null || this.renderState !== null) {
            throw new Error('reentrant Vellum host operations are not supported');
        }
        assertPlainJson(snapshot, 'state');
        if (!snapshot || snapshot.schema_version !== SNAPSHOT_SCHEMA ||
            snapshot.application_id !== this.applicationId ||
            typeof snapshot.layout_fingerprint !== 'string' ||
            !Array.isArray(snapshot.frames)) {
            throw new Error('Vellum state snapshot is incompatible with this application layout');
        }
        if (snapshot.application_state_version !== this.applicationStateVersion) {
            throw new Error(
                `Vellum state snapshot version ${snapshot.application_state_version ?? 'missing'} ` +
                `does not match application version ${this.applicationStateVersion}`,
            );
        }
        const frames = new Map();
        const seen = new Set();
        for (const source of snapshot.frames) {
            if (!source || typeof source.id !== 'string' || source.id.length === 0 ||
                !Array.isArray(source.hooks) || !Array.isArray(source.values) ||
                seen.has(source.id) ||
                source.hooks.some((kind) => kind !== 'state' && kind !== 'memo')) {
                throw new Error('Vellum state snapshot contains an invalid component frame');
            }
            seen.add(source.id);
            const frame = {
                established: true,
                cursor: 0,
                hooks: source.hooks.map((kind) => kind === 'state'
                    ? { kind: 'state', value: null }
                    : { kind: 'memo', initialized: false, value: undefined, dependencies: null }),
            };
            const expected = source.hooks.filter((kind) => kind === 'state').length;
            if (source.values.length !== expected) {
                throw new Error('Vellum state snapshot hook layout does not match');
            }
            const valueSlots = new Set();
            for (const entry of source.values) {
                if (!Number.isInteger(entry.slot) || entry.slot < 0 ||
                    entry.slot >= frame.hooks.length || frame.hooks[entry.slot].kind !== 'state' ||
                    valueSlots.has(entry.slot)) {
                    throw new Error('Vellum state snapshot hook slot does not match');
                }
                valueSlots.add(entry.slot);
                frame.hooks[entry.slot].value = durableValue(
                    entry.value, `${source.id}[${entry.slot}]`);
            }
            frames.set(source.id, frame);
        }
        const model = durableValue(snapshot.model ?? null, 'model');
        const candidate = this.renderCandidate(frames, model);
        if (frameFingerprint(candidate.frames) !== snapshot.layout_fingerprint) {
            throw new Error('Vellum state snapshot is incompatible with this application layout');
        }
        this.frames = candidate.frames;
        this.handlers = candidate.handlers;
        this.model = model;
        this.lastTree = candidate.tree;
        this.dirty = false;
        return this.lastTree;
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
            assertPlainJson(request.payload ?? null, 'payload');
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
            return stableStringify({ protocol: PROTOCOL, tree: runtime.restore(envelope.state) });
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
