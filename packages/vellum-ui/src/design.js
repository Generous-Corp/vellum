import { Button, Canvas, Image, Stack, Text, View, jsx } from './runtime.js';

const MAXIMUM_NODES = 100_000;
const MAXIMUM_DEPTH = 256;
const EVENT_PROPERTIES = Object.freeze({
    press: 'onPress',
    change: 'onChange',
    submit: 'onSubmit',
    keyDown: 'onKeyDown',
});

function plainObject(value, path) {
    if (value === null || typeof value !== 'object' || Array.isArray(value)) {
        throw new TypeError(`${path} must be an object`);
    }
    const prototype = Object.getPrototypeOf(value);
    if (prototype !== Object.prototype && prototype !== null) {
        throw new TypeError(`${path} must be a plain object`);
    }
    return value;
}

function tokenValue(tokens, value, stack = []) {
    if (typeof value !== 'string') return value;
    const match = /^\{([^{}]+)\}$/.exec(value);
    if (!match) return value;
    const name = match[1];
    if (stack.includes(name)) throw new Error(`design token cycle: ${[...stack, name].join(' -> ')}`);
    const token = tokens[name];
    if (!token || typeof token !== 'object' || !('$value' in token)) {
        throw new Error(`unresolved design token: ${name}`);
    }
    const resolved = tokenValue(tokens, token.$value, [...stack, name]);
    if (resolved && typeof resolved === 'object' &&
        typeof resolved.value === 'number' && resolved.unit === 'px') {
        return resolved.value;
    }
    return resolved;
}

function retainedStyle(document, node, viewport, root) {
    const tokens = document.tokens ?? {};
    const properties = node.properties && typeof node.properties === 'object'
        ? node.properties : {};
    const layout = properties.layout && typeof properties.layout === 'object'
        ? properties.layout : {};
    const paint = properties.paint && typeof properties.paint === 'object'
        ? properties.paint : {};
    const typography = properties.text && typeof properties.text === 'object'
        ? properties.text : {};
    const style = Object.create(null);
    for (const property of ['x', 'y', 'width', 'height', 'padding', 'gap']) {
        if (typeof layout[property] === 'number' && Number.isFinite(layout[property])) {
            style[property] = layout[property];
        }
    }
    if (layout.direction === 'row') style.direction = 'horizontal';
    else if (layout.direction === 'column') style.direction = 'vertical';
    if (typeof paint.backgroundColor === 'string') {
        style.backgroundColor = tokenValue(tokens, paint.backgroundColor);
    }
    if (typeof paint.borderRadius === 'number' || typeof paint.borderRadius === 'string') {
        const radius = tokenValue(tokens, paint.borderRadius);
        if (typeof radius === 'number' && Number.isFinite(radius)) style.borderRadius = radius;
    }
    if (typeof typography.color === 'string') {
        style.color = tokenValue(tokens, typography.color);
    }
    if (typeof typography.fontSize === 'number' && Number.isFinite(typography.fontSize)) {
        style.fontSize = typography.fontSize;
    }
    if (root) {
        style.width = viewport.width;
        style.height = viewport.height;
        if (style.padding === undefined) style.padding = viewport.padding;
    }
    return style;
}

function componentFor(node) {
    if (node.kind === 'button') return Button;
    if (node.kind === 'text') return Text;
    if (node.kind === 'image') return Image;
    if (node.kind === 'canvas') return Canvas;
    if (node.kind === 'view') {
        const display = node.properties?.layout?.display;
        return display === 'flex' || display === 'grid' ? Stack : View;
    }
    throw new Error(`unsupported materialized design node kind: ${node.kind}`);
}

function materializeNode(node, context, depth, root = false) {
    if (depth > MAXIMUM_DEPTH || ++context.nodes > MAXIMUM_NODES) {
        throw new Error('materialized design exceeds the node or depth limit');
    }
    plainObject(node, `design node at depth ${depth}`);
    if (typeof node.id !== 'string' || node.id.length === 0) {
        throw new Error('every materialized design node requires a stable id');
    }
    if (context.ids.has(node.id)) throw new Error(`duplicate materialized design id: ${node.id}`);
    context.ids.add(node.id);
    if (!Array.isArray(node.children)) throw new Error(`design node ${node.id} requires children`);

    const properties = {
        id: node.id,
        style: retainedStyle(context.document, node, context.viewport, root),
        accessibilityLabel: typeof node.name === 'string' ? node.name : undefined,
        children: node.children.map((child) => materializeNode(child, context, depth + 1)),
    };
    if (typeof node.text === 'string') properties.text = node.text;
    const bindings = context.actions[node.id];
    if (bindings !== undefined) {
        context.visitedActions.add(node.id);
        plainObject(bindings, `actions.${node.id}`);
        for (const [event, handler] of Object.entries(bindings)) {
            const property = EVENT_PROPERTIES[event];
            if (!property) throw new Error(`unsupported design event '${event}' for ${node.id}`);
            properties[property] = handler;
        }
    }
    return jsx(componentFor(node), properties, node.id);
}

function materialize(document, options, missingActionTarget) {
    plainObject(document, 'design');
    const root = document.root;
    const rootLayout = root?.properties?.layout &&
        typeof root.properties.layout === 'object' &&
        !Array.isArray(root.properties.layout)
        ? root.properties.layout : {};
    if (document.tokens !== undefined) plainObject(document.tokens, 'design.tokens');
    const actions = options.actions === undefined ? {} : plainObject(options.actions, 'options.actions');
    const viewport = {
        width: options.viewport?.width ?? rootLayout.width ?? 800,
        height: options.viewport?.height ?? rootLayout.height ?? 600,
        padding: options.viewport?.padding ?? rootLayout.padding ?? 0,
    };
    for (const [name, value] of Object.entries(viewport)) {
        if (typeof value !== 'number' || !Number.isFinite(value) || value < 0 ||
            (name !== 'padding' && value === 0)) {
            throw new TypeError(`viewport.${name} must be a valid finite dimension`);
        }
    }
    const context = {
        actions,
        document,
        ids: new Set(),
        nodes: 0,
        visitedActions: new Set(),
        viewport,
    };
    const result = materializeNode(root, context, 0, true);
    for (const nodeId of Object.keys(actions)) {
        if (!context.visitedActions.has(nodeId)) {
            throw new Error(missingActionTarget(nodeId));
        }
    }
    return result;
}

function namespacedTokens(document) {
    const tokens = document.tokens;
    const namespace = document.source?.namespace;
    if (!tokens || typeof namespace !== 'string' || namespace.length === 0) return tokens;
    const aliases = Object.create(null);
    for (const [name, token] of Object.entries(tokens)) {
        aliases[name] = token;
        const prefix = `${namespace}.`;
        if (name.startsWith(prefix)) aliases[name.slice(prefix.length)] ??= token;
    }
    return aliases;
}

function normalizedDocument(document) {
    if (!document || typeof document !== 'object' || Array.isArray(document)) return document;
    const tokens = namespacedTokens(document);
    return tokens === document.tokens ? document : { ...document, tokens };
}

export function materializeDesign(document, options = {}) {
    return materialize(
        normalizedDocument(document),
        options,
        (nodeId) => `materialized design action target is missing: ${nodeId}`,
    );
}

function resolvedDesignActions(document, bindings, actions) {
    if (bindings === null || bindings === undefined) {
        const resolved = Object.create(null);
        for (const [nodeId, handler] of Object.entries(actions)) {
            resolved[nodeId] = { press: handler };
        }
        return resolved;
    }
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
    const resolved = Object.create(null);
    for (const [index, binding] of bindings.bindings.entries()) {
        if (!binding || typeof binding !== 'object' || Array.isArray(binding) ||
            typeof binding.resolvedNodeId !== 'string' ||
            binding.resolvedNodeId.length === 0 ||
            typeof binding.action !== 'string' || binding.action.length === 0 ||
            typeof binding.event !== 'string') {
            throw new TypeError(`Design binding ${index} is invalid`);
        }
        if (!Object.hasOwn(EVENT_PROPERTIES, binding.event)) {
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
        const byEvent = resolved[binding.resolvedNodeId] ??= Object.create(null);
        if (Object.hasOwn(byEvent, binding.event)) {
            throw new Error(
                `duplicate Design binding for ${binding.resolvedNodeId}.${binding.event}`,
            );
        }
        byEvent[binding.event] = handler;
    }
    return resolved;
}

export function Design({ document, actions = {}, bindings = null }) {
    if (!document || typeof document !== 'object' || !document.root ||
        typeof actions !== 'object' || actions === null || Array.isArray(actions)) {
        throw new TypeError('Design requires a normalized document and an action map');
    }
    return materialize(
        normalizedDocument(document),
        { actions: resolvedDesignActions(document, bindings, actions) },
        (nodeId) => `Design binding target is missing from imported design: ${nodeId}`,
    );
}
