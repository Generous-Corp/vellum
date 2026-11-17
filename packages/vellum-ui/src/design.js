import { Button, Canvas, Image, Path, Plot, Stack, Text, TextRun, Vector, View, jsx } from './runtime.js';

const MAXIMUM_NODES = 100_000;
const MAXIMUM_DEPTH = 256;
const EVENT_PROPERTIES = Object.freeze({
    press: 'onPress',
    change: 'onChange',
    submit: 'onSubmit',
    keyDown: 'onKeyDown',
});
// DesignIR schema versions this materializer reads. Version 2 adds the node
// `compositing` object, 3 adds material references, 4 adds tracks, and 5
// adds plot bindings and 6 adds compiled vector assets; a document without
// `schemaVersion` is version 1.
const DESIGN_SCHEMA_VERSIONS = Object.freeze([1, 2, 3, 4, 5, 6]);
const COMPOSITING_VERSION = 2;
const PLOTS_VERSION = 5;
const VECTOR_ASSET_VERSION = 6;
const VECTOR_PAINT_KEYS = Object.freeze(['transform', 'clipPath', 'overflow']);
const PLOT_BINDING_KEYS = Object.freeze([
    'channel', 'source', 'slots', 'history', 'orientation', 'audioChannel', 'min', 'max',
    'baseline', 'lineWidth', 'barGap', 'peakWidth', 'lineColor', 'fillColor', 'peakColor',
]);
// The DesignIR blend modes, spelled as the retained `mixBlendMode` style and
// the renderer's scene spell them.
const BLEND_MODES = Object.freeze([
    'normal', 'multiply', 'screen', 'overlay', 'darken', 'lighten', 'difference', 'exclusion',
    'source_over', 'destination_in', 'destination_out',
    'hue', 'saturation', 'color', 'luminosity', 'color_dodge', 'color_burn', 'hard_light',
    'soft_light', 'clear', 'source', 'destination', 'source_in', 'source_out', 'source_atop',
    'destination_over', 'destination_atop', 'xor', 'plus',
]);
const MASK_MODES = Object.freeze(['alpha', 'luminance']);
const COMPOSITING_KEYS = Object.freeze(['blendMode', 'isolate', 'mask', 'maskRef', 'opacity']);

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

const TOKEN_COLOR = /^#(?:[0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/i;
const SHORT_COLOR = /^#[0-9a-f]{3,4}$/i;

function canonicalColor(value) {
    if (typeof value !== 'string' || !SHORT_COLOR.test(value)) return value;
    return `#${[...value.slice(1)].map((digit) => digit + digit).join('')}`;
}
const LAYER_NAMES = Object.freeze(['theme', 'semantic']);
const CANONICAL_NAMES = new WeakMap();

function canonicalName(tokens, name) {
    return CANONICAL_NAMES.get(tokens)?.get(tokens[name]) ?? name;
}

function layerEntry(layers, tokens, name) {
    if (!layers) return undefined;
    const names = [name, canonicalName(tokens, name)];
    for (const layer of LAYER_NAMES) {
        const table = layers[layer];
        if (!table || typeof table !== 'object') continue;
        for (const candidate of names) {
            if (Object.hasOwn(table, candidate)) return { layer, value: table[candidate] };
        }
    }
    return undefined;
}

function tokenValue(tokens, value, stack = [], layers = undefined) {
    if (typeof value !== 'string') return value;
    const match = /^\{([^{}]+)\}$/.exec(value);
    if (!match) return value;
    const name = match[1];
    if (stack.includes(name)) throw new Error(`design token cycle: ${[...stack, name].join(' -> ')}`);
    const override = layerEntry(layers, tokens, name);
    if (override !== undefined) {
        return tokenValue(tokens, override.value, [...stack, name], layers);
    }
    const token = tokens[name];
    if (!token || typeof token !== 'object' || !('$value' in token)) {
        throw new Error(`unresolved design token: ${name}`);
    }
    const resolved = tokenValue(tokens, token.$value, [...stack, name], layers);
    if (resolved && typeof resolved === 'object' &&
        typeof resolved.value === 'number' && resolved.unit === 'px') {
        return resolved.value;
    }
    return resolved;
}

function tokenLayer(tokens, value, layers, stack = []) {
    const match = typeof value === 'string' ? /^\{([^{}]+)\}$/.exec(value) : null;
    if (!match) return undefined;
    const name = match[1];
    if (stack.includes(name)) return undefined;
    const override = layerEntry(layers, tokens, name);
    if (override !== undefined) {
        return tokenLayer(tokens, override.value, layers, [...stack, name]) ?? override.layer;
    }
    const token = tokens[name];
    if (!token || typeof token !== 'object' || !('$value' in token)) return undefined;
    return tokenLayer(tokens, token.$value, layers, [...stack, name]);
}

function colorToken(context, value) {
    const resolved = canonicalColor(
        tokenValue(context.document.tokens ?? {}, value, [], context.tokenLayers));
    const match = typeof value === 'string' ? /^\{([^{}]+)\}$/.exec(value) : null;
    if (!match || typeof resolved !== 'string' || !TOKEN_COLOR.test(resolved)) {
        return { value: resolved };
    }
    const name = match[1];
    const tokens = context.document.tokens;
    return {
        value: resolved,
        receipt: {
            layer: tokenLayer(tokens, value, context.tokenLayers) ?? 'primitive',
            path: canonicalName(tokens, name),
            value: resolved,
        },
    };
}

function paintNumbers(source, names) {
    const output = {};
    for (const name of names) {
        if (typeof source[name] === 'number' && Number.isFinite(source[name])) {
            output[name] = source[name];
        }
    }
    return output;
}

function colorSlot(context, refs, slot, value) {
    const color = colorToken(context, value);
    if (color.receipt) refs[slot] = color.receipt;
    return color.value;
}

// Every gradient field the native materializer understands. Each is forwarded
// verbatim so the materializer validates it; any other field is refused rather
// than silently dropped.
const GRADIENT_FIELDS = new Set([
    'kind', 'angle', 'repeating', 'repeatLength', 'spread', 'interpolation', 'colorSpace',
    'centerX', 'centerY', 'radiusX', 'radiusY', 'focalX', 'focalY', 'startRadius', 'endRadius',
]);

// The stroke attributes of the public Path contract, forwarded verbatim so the
// native materializer validates them.
const PATH_STROKE_FIELDS = Object.freeze([
    'strokeWidth', 'strokeLineJoin', 'strokeLineCap', 'strokeMiterLimit',
    'strokeDasharray', 'strokeDashoffset',
]);

function retainedGradient(context, refs, prefix, gradient) {
    if (!gradient || typeof gradient !== 'object' || !Array.isArray(gradient.stops)) {
        throw new TypeError(`${prefix} must hold a stops array`);
    }
    const output = {};
    for (const [field, value] of Object.entries(gradient)) {
        if (field === 'stops' || value === undefined) continue;
        if (!GRADIENT_FIELDS.has(field)) {
            throw new TypeError(`${prefix}.${field} is not a supported gradient field`);
        }
        output[field] = value;
    }
    output.stops = gradient.stops.map((stop, index) => {
        for (const field of Object.keys(stop ?? {})) {
            if (field !== 'position' && field !== 'color' && stop[field] !== undefined) {
                throw new TypeError(`${prefix}.stops.${index}.${field} is not supported`);
            }
        }
        return {
            position: stop?.position,
            color: colorSlot(context, refs, `${prefix}.stops.${index}`, stop?.color),
        };
    });
    return output;
}

const PATH_PAINT_FIELDS = new Set(['fill', 'stroke', ...PATH_STROKE_FIELDS]);
const PATH_GEOMETRY_FIELDS = new Set(['d', 'fillRule']);

function requirePathFields(node, name, value, allowed) {
    for (const field of Object.keys(value)) {
        if (!allowed.has(field) && value[field] !== undefined) {
            throw new TypeError(`design path ${node.id} ${name}.${field} is not supported`);
        }
    }
}

function pathPaint(context, node, refs) {
    const properties = node.properties && typeof node.properties === 'object'
        ? node.properties : {};
    const paint = properties.paint && typeof properties.paint === 'object'
        ? properties.paint : {};
    const geometry = properties.path && typeof properties.path === 'object'
        ? properties.path : {};
    requirePathFields(node, 'paint', paint, PATH_PAINT_FIELDS);
    requirePathFields(node, 'path', geometry, PATH_GEOMETRY_FIELDS);
    const output = { d: geometry.d };
    if (typeof geometry.fillRule === 'string') output.fillRule = geometry.fillRule;
    if (paint.fill !== undefined) {
        output.fill = paint.fill && typeof paint.fill === 'object'
            ? retainedGradient(context, refs, 'fill', paint.fill)
            : colorSlot(context, refs, 'fill', paint.fill);
    }
    if (paint.stroke !== undefined) output.stroke = colorSlot(context, refs, 'stroke', paint.stroke);
    for (const field of PATH_STROKE_FIELDS) {
        const value = paint[field];
        if (value === undefined) continue;
        output[field] = Array.isArray(value) ? [...value] : value;
    }
    return output;
}

function retainedStyle(context, node, viewport, root) {
    const { document } = context;
    const tokens = document.tokens ?? {};
    const refs = Object.create(null);
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
    const drawsBox = node.kind !== 'path' && node.kind !== 'text-run';
    if (drawsBox && typeof paint.backgroundColor === 'string') {
        style.backgroundColor = colorSlot(context, refs, 'backgroundColor', paint.backgroundColor);
    }
    if (drawsBox && paint.boxShadow !== undefined) {
        const shadow = paint.boxShadow;
        if (!shadow || typeof shadow !== 'object') {
            throw new TypeError(`design node ${node.id} paint.boxShadow must be an object`);
        }
        style.boxShadow = {
            ...paintNumbers(shadow, ['offsetX', 'offsetY', 'blurRadius', 'spreadRadius']),
            color: colorSlot(context, refs, 'boxShadow', shadow.color),
        };
    }
    if (drawsBox && paint.backgroundLinearGradient !== undefined) {
        style.backgroundLinearGradient = retainedGradient(
            context, refs, 'backgroundLinearGradient', paint.backgroundLinearGradient);
    }
    if (typeof paint.borderRadius === 'number' || typeof paint.borderRadius === 'string') {
        const radius = tokenValue(tokens, paint.borderRadius, [], context.tokenLayers);
        if (typeof radius === 'number' && Number.isFinite(radius)) style.borderRadius = radius;
    }
    if (typeof typography.color === 'string' && node.kind !== 'path') {
        style.color = colorSlot(context, refs, 'color', typography.color);
    }
    if (typeof typography.fontSize === 'number' && Number.isFinite(typography.fontSize)) {
        style.fontSize = typography.fontSize;
    }
    for (const property of VECTOR_PAINT_KEYS) {
        if (typeof paint[property] === 'string') style[property] = paint[property];
    }
    if (root) {
        style.width = viewport.width;
        style.height = viewport.height;
        if (style.padding === undefined) style.padding = viewport.padding;
    }
    return { style, refs };
}

function designVersion(document) {
    const version = document.schemaVersion ?? 1;
    if (!DESIGN_SCHEMA_VERSIONS.includes(version)) {
        throw new Error(`unsupported DesignIR schemaVersion: ${String(version)}`);
    }
    return version;
}

// The first vector asset field a node uses, which needs version 6, or null.
// Path geometry and the path kind are part of the version 1-5 path contract.
function vectorAssetField(node) {
    if (node.kind === 'vector') return `kind ${node.kind}`;
    const properties = node.properties && typeof node.properties === 'object' ? node.properties : {};
    if (properties.vector !== undefined) return 'properties.vector';
    if (properties.image && typeof properties.image === 'object' &&
        properties.image.source !== undefined) {
        return 'properties.image.source';
    }
    const paint = properties.paint && typeof properties.paint === 'object' ? properties.paint : {};
    const key = VECTOR_PAINT_KEYS.find((name) => paint[name] !== undefined);
    return key === undefined ? null : `properties.paint.${key}`;
}

// The node's validated `compositing` object, or undefined. It is version 2
// vocabulary, and a mask, which is coverage rather than paint, takes no blend
// mode but normal.
function compositingOf(node, version, root) {
    if (node.compositing === undefined) return undefined;
    const where = `design node ${node.id} compositing`;
    if (version < COMPOSITING_VERSION) {
        throw new Error(`${where} needs schemaVersion ${COMPOSITING_VERSION}`);
    }
    const compositing = plainObject(node.compositing, where);
    for (const key of Object.keys(compositing)) {
        if (!COMPOSITING_KEYS.includes(key)) throw new Error(`${where} has unknown field '${key}'`);
    }
    const { blendMode, isolate, mask, maskRef, opacity } = compositing;
    if (opacity !== undefined &&
        (typeof opacity !== 'number' || !Number.isFinite(opacity) || opacity < 0 || opacity > 1)) {
        throw new TypeError(`${where}.opacity must be a number from 0 to 1`);
    }
    if (blendMode !== undefined && !BLEND_MODES.includes(blendMode)) {
        throw new TypeError(`${where}.blendMode must be a known blend mode`);
    }
    if (isolate !== undefined && typeof isolate !== 'boolean') {
        throw new TypeError(`${where}.isolate must be a boolean`);
    }
    if (mask !== undefined && !MASK_MODES.includes(mask)) {
        throw new TypeError(`${where}.mask must be alpha or luminance`);
    }
    if (maskRef !== undefined && (typeof maskRef !== 'string' || maskRef.length === 0)) {
        throw new TypeError(`${where}.maskRef must be a non-empty string`);
    }
    if (mask !== undefined && blendMode !== undefined && blendMode !== 'normal') {
        throw new Error(`${where}: a mask node takes no blend mode but normal`);
    }
    if (root && (mask !== undefined || maskRef !== undefined)) {
        throw new Error(`${where}: the root has no siblings to mask or be masked by`);
    }
    return compositing;
}

// Lowers compositing into the retained styles the renderer reads: `opacity`,
// `mixBlendMode`, `isolation` and `maskMode`. `maskRef` has no style; it is
// resolved against the siblings by `resolveMaskReferences`.
function applyCompositing(style, compositing) {
    if (compositing === undefined) return;
    if (compositing.opacity !== undefined) style.opacity = compositing.opacity;
    if (compositing.blendMode !== undefined && compositing.blendMode !== 'normal') {
        style.mixBlendMode = compositing.blendMode;
    }
    if (compositing.isolate === true) style.isolation = 'isolate';
    if (compositing.mask !== undefined) style.maskMode = compositing.mask;
}

// The renderer applies a mask to every sibling of the mask node, while
// DesignIR names the mask each node is masked by. The two agree only when a
// parent has at most one mask and every other child names it; anything else
// cannot be drawn as written and is refused rather than drawn differently.
function resolveMaskReferences(node, version) {
    if (version < COMPOSITING_VERSION) return;
    const children = node.children.filter((child) => child && typeof child === 'object');
    const masks = children.filter((child) => child.compositing?.mask !== undefined);
    for (const child of children) {
        const reference = child.compositing?.maskRef;
        if (reference === undefined) continue;
        if (child.compositing.mask !== undefined) {
            throw new Error(`design node ${child.id} is a mask and cannot reference another mask`);
        }
        if (!masks.some((mask) => mask.id === reference)) {
            throw new Error(
                `design node ${child.id} maskRef '${String(reference)}' does not name a sibling mask`,
            );
        }
    }
    if (masks.length === 0) return;
    if (masks.length > 1) {
        throw new Error(
            `design node ${node.id} has ${masks.length} mask children; the renderer masks every ` +
            'sibling with each mask, so one parent can hold only one',
        );
    }
    const [mask] = masks;
    for (const child of children) {
        if (child === mask || child.compositing?.maskRef === mask.id) continue;
        throw new Error(
            `design node ${child.id} is not masked by ${mask.id}, but the renderer masks every ` +
            `sibling of a mask; give it maskRef '${mask.id}' or move it under another parent`,
        );
    }
}

function componentFor(node) {
    if (node.plot !== undefined) return Plot;
    if (node.kind === 'button') return Button;
    if (node.kind === 'text') return Text;
    if (node.kind === 'image') return Image;
    if (node.kind === 'canvas') return Canvas;
    if (node.kind === 'text-run') return TextRun;
    if (node.kind === 'path') return Path;
    if (node.kind === 'vector') return Vector;
    if (node.kind === 'view') {
        const display = node.properties?.layout?.display;
        return display === 'flex' || display === 'grid' ? Stack : View;
    }
    throw new Error(`unsupported materialized design node kind: ${node.kind}`);
}

// The identity the design tool gave the node, so a rendered node can be
// traced back to it: `figma:<node id>` for a Figma provider id, the adapter or
// `semantic` prefix for other sources, and the stable design id otherwise.
function anchorFor(node, document) {
    const identity = node.identity;
    if (identity?.strategy === 'provider' && typeof identity.sourceId === 'string') {
        const adapter = document.source?.adapter;
        const prefix = typeof adapter === 'string' && adapter.startsWith('figma')
            ? 'figma' : (typeof adapter === 'string' && adapter ? adapter : 'provider');
        return `${prefix}:${identity.sourceId}`;
    }
    if (identity?.strategy === 'semantic' && typeof identity.semanticId === 'string') {
        return `semantic:${identity.semanticId}`;
    }
    return node.id;
}

// The node's plot binding, which is version 5 and draws no children.
function plotBindingOf(node, version) {
    if (version < PLOTS_VERSION) {
        throw new Error(`design node ${node.id} plot needs schemaVersion ${PLOTS_VERSION}`);
    }
    plainObject(node.plot, `design node ${node.id} plot`);
    if (node.children.length > 0) {
        throw new Error(`design node ${node.id} binds a plot and cannot have children`);
    }
    return node.plot;
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
    const { style, refs } = retainedStyle(context, node, context.viewport, root);
    if (node.kind === 'vector') delete refs.backgroundColor;
    const vectorField = vectorAssetField(node);
    if (vectorField !== null && context.version < VECTOR_ASSET_VERSION) {
        throw new Error(
            `design node ${node.id} ${vectorField} needs schemaVersion ${VECTOR_ASSET_VERSION}`);
    }
    applyCompositing(style, compositingOf(node, context.version, root));
    resolveMaskReferences(node, context.version);
    if ((node.kind === 'text-run' || node.kind === 'path') && node.children.length > 0) {
        throw new Error(`design ${node.kind} ${node.id} does not accept children`);
    }
    if (node.kind === 'text-run') {
        if (context.actions[node.id] !== undefined) {
            throw new Error(`design text-run ${node.id} does not accept actions`);
        }
        const run = { style, text: typeof node.text === 'string' ? node.text : '' };
        if (Object.keys(refs).length > 0) run.tokenRefs = { ...refs };
        return jsx(TextRun, run, node.id);
    }
    const properties = {
        id: node.id,
        anchor: anchorFor(node, context.document),
        style,
        accessibilityLabel: typeof node.name === 'string' ? node.name : undefined,
        // A source node that the design tool laid out as `display: none` has
        // no box, so it is not rendered.
        children: node.children
            .filter((child) => child?.properties?.layout?.display !== 'none')
            .map((child) => materializeNode(child, context, depth + 1)),
    };
    if (node.kind === 'path') Object.assign(properties, pathPaint(context, node, refs));
    if (Object.keys(refs).length > 0) properties.tokenRefs = { ...refs };
    if (typeof node.text === 'string') properties.text = node.text;
    if (node.kind === 'vector') {
        const vector = node.properties?.vector;
        if (!vector || typeof vector !== 'object' || !vector.asset) {
            throw new Error(`design vector ${node.id} requires a compiled asset`);
        }
        properties.asset = vector.asset;
    }
    if (node.kind === 'image' && typeof node.properties?.image?.source === 'string') {
        properties.source = node.properties.image.source;
        if (typeof node.properties.image.fit === 'string') properties.fit = node.properties.image.fit;
    }
    if (node.plot !== undefined) {
        const plot = plotBindingOf(node, context.version);
        properties.plot = plot.style;
        for (const key of PLOT_BINDING_KEYS) {
            if (plot[key] !== undefined) properties[key] = plot[key];
        }
    }
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

// Check even hidden descendants before lowering; an unsupported field must
// never disappear silently when this consumer reads a newer document.
function requireSupportedFields(root, version) {
    let nodes = 0;
    function visit(node, depth) {
        if (depth > MAXIMUM_DEPTH || ++nodes > MAXIMUM_NODES) {
            throw new Error('materialized design exceeds the node or depth limit');
        }
        plainObject(node, `design node at depth ${depth}`);
        if (node.material !== undefined) {
            throw new Error(`design node ${node.id} has unsupported material`);
        }
        if (node.plot !== undefined && version < PLOTS_VERSION) {
            throw new Error(`design node ${node.id} plot needs schemaVersion ${PLOTS_VERSION}`);
        }
        const vectorField = vectorAssetField(node);
        if (vectorField !== null && version < VECTOR_ASSET_VERSION) {
            throw new Error(
                `design node ${node.id} ${vectorField} needs schemaVersion ${VECTOR_ASSET_VERSION}`);
        }
        if (node.tracks !== undefined && (!Array.isArray(node.tracks) || node.tracks.length > 0)) {
            throw new Error(`design node ${node.id} has unsupported tracks`);
        }
        if (!Array.isArray(node.children)) throw new Error(`design node ${node.id} requires children`);
        for (const child of node.children) visit(child, depth + 1);
    }
    visit(root, 0);
}

function materialize(document, options, missingActionTarget) {
    plainObject(document, 'design');
    const version = designVersion(document);
    const root = document.root;
    requireSupportedFields(root, version);
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
        version,
        tokenLayers: options.tokenLayers,
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
    const names = new Map();
    CANONICAL_NAMES.set(aliases, names);
    for (const [name, token] of Object.entries(tokens)) {
        aliases[name] = token;
        if (!names.has(token)) names.set(token, name);
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

export function Design({ document, actions = {}, bindings = null, tokenLayers = undefined }) {
    if (!document || typeof document !== 'object' || !document.root ||
        typeof actions !== 'object' || actions === null || Array.isArray(actions)) {
        throw new TypeError('Design requires a normalized document and an action map');
    }
    return materialize(
        normalizedDocument(document),
        { actions: resolvedDesignActions(document, bindings, actions), tokenLayers },
        (nodeId) => `Design binding target is missing from imported design: ${nodeId}`,
    );
}
