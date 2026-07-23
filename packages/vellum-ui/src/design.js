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

function retainedStyle(node, tokens, viewport, root) {
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
        style: retainedStyle(node, context.tokens, context.viewport, root),
        accessibilityLabel: typeof node.name === 'string' ? node.name : undefined,
        children: node.children.map((child) => materializeNode(child, context, depth + 1)),
    };
    if (typeof node.text === 'string') properties.text = node.text;
    const bindings = context.actions[node.id];
    if (bindings !== undefined) {
        plainObject(bindings, `actions.${node.id}`);
        for (const [event, handler] of Object.entries(bindings)) {
            const property = EVENT_PROPERTIES[event];
            if (!property) throw new Error(`unsupported design event '${event}' for ${node.id}`);
            properties[property] = handler;
        }
    }
    return jsx(componentFor(node), properties, node.id);
}

export function materializeDesign(document, options = {}) {
    plainObject(document, 'design');
    const root = document.root;
    const rootLayout = root?.properties?.layout &&
        typeof root.properties.layout === 'object' &&
        !Array.isArray(root.properties.layout)
        ? root.properties.layout : {};
    const tokens = document.tokens === undefined ? {} : plainObject(document.tokens, 'design.tokens');
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
    return materializeNode(root, {
        actions,
        ids: new Set(),
        nodes: 0,
        tokens,
        viewport,
    }, 0, true);
}
