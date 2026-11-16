// Draws the canvas shell's plan for one compiled vector asset. The plan is
// produced by the shared C++ core (`lower_vector_for_canvas`): paths are
// absolute SVG path data, linear gradients arrive as endpoints in the
// operation's coordinates, and a radial fill that a non-uniform transform
// turns elliptical arrives in its own `space`, so Path2D draws it exactly.

function cssColor([red, green, blue, alpha]) {
    return `rgba(${Math.round(red * 255)},${Math.round(green * 255)},` +
        `${Math.round(blue * 255)},${alpha})`;
}

function invert([a, b, c, d, e, f]) {
    const determinant = a * d - b * c;
    return new DOMMatrix([d / determinant, -b / determinant, -c / determinant,
        a / determinant, (c * f - d * e) / determinant, (b * e - a * f) / determinant]);
}

function style(context, paint) {
    if (paint.color) return cssColor(paint.color);
    const gradient = paint.linear
        ? context.createLinearGradient(...paint.linear)
        : context.createRadialGradient(...paint.radial);
    for (const [position, ...color] of paint.stops) gradient.addColorStop(position, cssColor(color));
    return gradient;
}

function layerFor(canvas) {
    if (typeof OffscreenCanvas === 'function') return new OffscreenCanvas(canvas.width, canvas.height);
    const layer = canvas.ownerDocument.createElement('canvas');
    layer.width = canvas.width;
    layer.height = canvas.height;
    return layer;
}

// The node's opacity applies to the asset as one group: a translucent asset
// draws offscreen at full alpha and meets the canvas once, so its
// overlapping paths do not show through one another.
export function drawVector(context, plan) {
    const alpha = plan.alpha ?? 1;
    if (alpha >= 1) {
        drawSteps(context, plan);
        return;
    }
    if (alpha <= 0) return;
    const layer = layerFor(context.canvas);
    const layerContext = layer.getContext('2d');
    layerContext.setTransform(context.getTransform());
    drawSteps(layerContext, plan);
    context.save();
    context.setTransform(1, 0, 0, 1, 0, 0);
    context.globalAlpha *= alpha;
    context.drawImage(layer, 0, 0);
    context.restore();
}

function drawSteps(context, plan) {
    context.save();
    context.transform(...plan.matrix);
    for (const step of plan.steps) {
        if (step.op === 'save') {
            context.save();
            if (step.transform) context.transform(...step.transform);
            context.globalAlpha *= step.alpha;
        } else if (step.op === 'restore') {
            context.restore();
        } else if (step.op === 'clip') {
            context.clip(new Path2D(step.d), step.rule);
        } else if (step.op === 'fill') {
            context.save();
            if (step.transform) context.transform(...step.transform);
            let path = new Path2D(step.d);
            if (step.paint.space) {
                const moved = new Path2D();
                moved.addPath(path, invert(step.paint.space));
                path = moved;
                context.transform(...step.paint.space);
            }
            context.fillStyle = style(context, step.paint);
            context.fill(path, step.rule);
            context.restore();
        } else if (step.op === 'stroke') {
            context.save();
            if (step.transform) context.transform(...step.transform);
            context.lineWidth = step.width;
            context.lineJoin = step.join;
            context.lineCap = step.cap;
            context.miterLimit = step.miter;
            if (step.dash) {
                context.setLineDash(step.dash);
                context.lineDashOffset = step.dashOffset ?? 0;
            }
            context.strokeStyle = style(context, step.paint);
            context.stroke(new Path2D(step.d));
            context.restore();
        }
    }
    context.restore();
}

const BUILDER_SIGNATURES = {
    begin: ['vellum_web_begin_vector', ['string', 'number', 'number', 'number', 'number',
        'number', 'number', 'number', 'number', 'number', 'string']],
    group: ['vellum_web_vector_group', ['number', 'number', 'number', 'number', 'number',
        'number', 'number', 'number', 'string', 'number']],
    end: ['vellum_web_vector_end_group', []],
    path: ['vellum_web_vector_path', ['string', 'number', ...Array(19).fill('number')]],
    dash: ['vellum_web_vector_dash', ['number']],
    gradient: ['vellum_web_vector_gradient', Array(20).fill('number')],
    stop: ['vellum_web_vector_stop', ['number', 'number', 'number', 'number', 'number', 'number']],
    finish: ['vellum_web_end_vector', []],
};

function rgba(hex) {
    if (typeof hex !== 'string' || hex === 'none') return [0, 0, 0, 0];
    const value = hex.slice(1);
    const channels = [0, 2, 4].map((offset) => Number.parseInt(value.slice(offset, offset + 2), 16) / 255);
    return [...channels, value.length === 8 ? Number.parseInt(value.slice(6, 8), 16) / 255 : 1];
}

const IDENTITY = [1, 0, 0, 1, 0, 0];
const KINDS = { linear: 0, radial: 1, 'two-circle-radial': 2, conic: 3 };
const INTERPOLATIONS = { srgb: 0, 'linear-srgb': 1, oklab: 2 };
const COLOR_SPACES = { srgb: 0, 'linear-srgb': 1, 'display-p3': 2 };

function code(table, value, fallback, what) {
    const key = value ?? fallback;
    if (!Object.hasOwn(table, key)) throw new Error(`the web shell cannot carry the ${what} ${key}`);
    return table[key];
}
const SPREADS = { pad: 0, repeat: 1, reflect: 2 };
const JOINS = { miter: 0, round: 1, bevel: 2 };
const CAPS = { butt: 0, round: 1, square: 2 };

// Adds a rendered Vector node to the shell's scene, with the node's own
// opacity, which applies to the asset as one group.
export function addVectorNode(module, node, x, y, width, height) {
    const opacity = node.style?.opacity;
    addVectorAsset(module, node.id, x, y, width, height, node.asset,
        typeof opacity === 'number' && Number.isFinite(opacity) ? Math.min(1, Math.max(0, opacity)) : 1);
}

// Adds a compiled vector asset, in the form the runtime's Vector primitive
// carries, to the shell's scene as one vector node.
export function addVectorAsset(module, id, x, y, width, height, asset, opacity = 1) {
    const api = Object.fromEntries(Object.entries(BUILDER_SIGNATURES).map(([name, [symbol, args]]) =>
        [name, module.cwrap(symbol, 'number', args)]));
    const ok = (value, what) => {
        if (!value) throw new Error(`the web shell refused ${what} of vector ${id}`);
    };
    ok(api.begin(id, x, y, width, height, opacity, ...asset.viewBox, asset.contentHash), 'the asset');
    for (const op of asset.ops) {
        if (op.op === 'group') {
            ok(api.group(...(op.transform ?? IDENTITY), op.opacity ?? 1, op.clip ? 1 : 0,
                op.clip?.d ?? '', op.clip?.rule === 'evenodd' ? 1 : 0), 'a group');
        } else if (op.op === 'end') {
            ok(api.end(), 'a group end');
        } else {
            const fill = typeof op.fill === 'object' ? [0, 0, 0, 0] : rgba(op.fill ?? '#000000');
            const stroke = typeof op.stroke === 'object' ? [0, 0, 0, 0] : rgba(op.stroke);
            ok(api.path(op.d, op.fillRule === 'evenodd' ? 1 : 0, ...(op.transform ?? IDENTITY),
                ...fill, ...stroke, op.strokeWidth ?? 1, code(JOINS, op.strokeLineJoin, 'miter', 'join'),
                code(CAPS, op.strokeLineCap, 'butt', 'cap'), op.strokeMiterLimit ?? 4,
                op.strokeDashoffset ?? 0), 'a path');
            for (const dash of op.strokeDasharray ?? []) ok(api.dash(dash), 'a dash');
            for (const [target, paint] of [[0, op.fill], [1, op.stroke]]) {
                if (!paint || typeof paint !== 'object') continue;
                const g = paint.gradient;
                if (g.repeatLength) throw new Error(`vector ${id} gradients cannot set a repeat length`);
                const spread = g.spread ?? (g.repeating ? 'repeat' : 'pad');
                ok(api.gradient(target, code(KINDS, g.kind, 'linear', 'gradient kind'),
                    code(SPREADS, spread, 'pad', 'spread'),
                    code(INTERPOLATIONS, g.interpolation, 'srgb', 'interpolation'),
                    code(COLOR_SPACES, g.colorSpace, 'srgb', 'colour space'), g.angle ?? 180, g.centerX ?? 0.5, g.centerY ?? 0.5, g.radiusX ?? 0,
                    g.radiusY ?? 0, g.focalX ?? 0.5, g.focalY ?? 0.5, g.startRadius ?? 0,
                    g.endRadius ?? 0.5, ...(paint.transform ?? IDENTITY)), 'a gradient');
                for (const stop of g.stops) {
                    ok(api.stop(target, stop.position, ...rgba(stop.color)), 'a gradient stop');
                }
            }
        }
    }
    ok(api.finish(), 'the end');
}
