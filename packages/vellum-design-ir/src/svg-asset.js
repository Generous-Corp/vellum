// A bounded SVG sanitizer. Its output uses the same path and group primitives
// as an authored DesignIR tree, so identities and tokens survive reimport.
const DEFAULT_NODE_BUDGET = 512;
const DEFAULT_PATH_DATA_BUDGET = 65536;
const MAX_SVG_BYTES = 8 * 1024 * 1024;
const CONTAINERS = new Set(['svg', 'g', 'defs', 'clipPath', 'mask', 'symbol']);
const SHAPES = new Set(['path', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon']);
const DEFINITIONS = new Set(['linearGradient', 'radialGradient', 'stop']);
const PRESENTATION = new Set(['fill', 'stroke', 'stroke-width', 'fill-rule', 'opacity',
    'transform', 'clip-path', 'mask', 'stroke-linecap', 'stroke-linejoin',
    'stroke-miterlimit', 'stroke-dasharray', 'stroke-dashoffset']);

function xmlText(value) {
    return value.replace(/&(#x[0-9a-f]+|#[0-9]+|amp|lt|gt|quot|apos);/gi, (entity, name) => {
        if (name[0] === '#') {
            const code = name[1].toLowerCase() === 'x'
                ? Number.parseInt(name.slice(2), 16) : Number.parseInt(name.slice(1), 10);
            if (!Number.isFinite(code) || code === 0 || code > 0x10ffff ||
                (code >= 0xd800 && code <= 0xdfff)) throw new TypeError('invalid SVG entity');
            return String.fromCodePoint(code);
        }
        return { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'" }[name.toLowerCase()];
    }).replace(/&(?!(?:#x[0-9a-f]+|#[0-9]+|amp|lt|gt|quot|apos);)/i, () => {
        throw new TypeError('unsupported SVG entity');
    });
}

function parseXml(svg) {
    if (typeof svg !== 'string' || new TextEncoder().encode(svg).length > MAX_SVG_BYTES ||
        /<!|<\?|<\s*script\b/i.test(svg)) throw new TypeError('unsafe or oversized SVG');
    const root = { name: '#document', attributes: {}, children: [] };
    const stack = [root];
    const tags = /<[^>]*>/g;
    let cursor = 0;
    for (const match of svg.matchAll(tags)) {
        if (svg.slice(cursor, match.index).trim()) {
            throw new TypeError('SVG text content is unsupported');
        }
        const tag = match[0];
        cursor = match.index + tag.length;
        if (/^<\//.test(tag)) {
            const name = /^<\/([A-Za-z][\w:.-]*)\s*>$/.exec(tag)?.[1];
            if (!name || stack.length < 2 || stack.at(-1).name !== name) {
                throw new TypeError('malformed SVG closing tag');
            }
            stack.pop();
            continue;
        }
        const opening = /^<([A-Za-z][\w:.-]*)\b/.exec(tag);
        if (!opening) throw new TypeError('malformed SVG tag');
        const name = opening[1];
        const attributes = Object.create(null);
        const end = /\/\s*>$/.test(tag) ? tag.lastIndexOf('/') : tag.length - 1;
        const body = tag.slice(opening[0].length, end);
        const attribute = /\s+([A-Za-z_:][\w:.-]*)\s*=\s*(?:"([^"]*)"|'([^']*)')/gy;
        let offset = 0;
        while (offset < body.length) {
            attribute.lastIndex = offset;
            const found = attribute.exec(body);
            if (!found) {
                if (body.slice(offset).trim()) throw new TypeError('malformed SVG attribute');
                break;
            }
            if (Object.hasOwn(attributes, found[1])) throw new TypeError('duplicate SVG attribute');
            attributes[found[1]] = xmlText(found[2] ?? found[3]);
            offset = attribute.lastIndex;
        }
        const node = { name, attributes, children: [] };
        stack.at(-1).children.push(node);
        if (!/\/\s*>$/.test(tag)) stack.push(node);
        if (stack.length > 128) throw new TypeError('SVG nesting exceeds limit');
    }
    if (svg.slice(cursor).trim() || stack.length !== 1 || root.children.length !== 1 ||
        root.children[0].name !== 'svg') throw new TypeError('malformed SVG document');
    return root.children[0];
}

function finite(value, fallback = 0) {
    if (value === undefined) return fallback;
    if (!/^[+-]?(?:\d+\.?\d*|\.\d+)$/.test(value)) throw new TypeError('invalid SVG number');
    const number = Number(value);
    if (!Number.isFinite(number)) throw new TypeError('non-finite SVG number');
    return number;
}

function points(value) {
    const numbers = value.trim().split(/[\s,]+/).filter(Boolean).map((part) => finite(part));
    if (numbers.length < 4 || numbers.length % 2) throw new TypeError('invalid SVG points');
    return numbers;
}

function shapePath(node) {
    const a = node.attributes;
    const x = finite(a.x), y = finite(a.y);
    switch (node.name) {
    case 'path': return a.d ?? '';
    case 'rect': {
        const w = finite(a.width), h = finite(a.height);
        if (w < 0 || h < 0) throw new TypeError('negative SVG rectangle size');
        return `M${x} ${y}h${w}v${h}h${-w}Z`;
    }
    case 'line': return `M${finite(a.x1)} ${finite(a.y1)}L${finite(a.x2)} ${finite(a.y2)}`;
    case 'polyline':
    case 'polygon': {
        const p = points(a.points ?? '');
        let d = `M${p[0]} ${p[1]}`;
        for (let i = 2; i < p.length; i += 2) d += `L${p[i]} ${p[i + 1]}`;
        return d + (node.name === 'polygon' ? 'Z' : '');
    }
    case 'circle':
    case 'ellipse': {
        const cx = finite(a.cx), cy = finite(a.cy);
        const rx = finite(node.name === 'circle' ? a.r : a.rx);
        const ry = finite(node.name === 'circle' ? a.r : a.ry);
        if (rx < 0 || ry < 0) throw new TypeError('negative SVG radius');
        return `M${cx - rx} ${cy}a${rx} ${ry} 0 1 0 ${2 * rx} 0a${rx} ${ry} 0 1 0 ${-2 * rx} 0Z`;
    }
    default: throw new TypeError('unsupported SVG shape');
    }
}

function diagnostic(code, path, message, disposition = 'unsupported') {
    return { code, path, message, disposition, severity: 'warning' };
}

function fraction(value, fallback) {
    if (value === undefined) return fallback;
    return value.endsWith('%') ? finite(value.slice(0, -1)) / 100 : finite(value);
}

function color(value) {
    if (/^#[0-9a-fA-F]{3}$/.test(value)) {
        return '#' + [...value.slice(1)].map((digit) => digit + digit).join('');
    }
    return value;
}

function gradient(definition, path, diagnostics) {
    if (!['linearGradient', 'radialGradient'].includes(definition?.name)) return null;
    if (definition.attributes.gradientUnits &&
        definition.attributes.gradientUnits !== 'objectBoundingBox') {
        diagnostics.push(diagnostic('svg-gradient-units-dropped', path,
            'SVG userSpaceOnUse gradient coordinates require a compiled resource'));
        return null;
    }
    const stops = [];
    for (const stop of definition.children) {
        if (stop.name !== 'stop') {
            diagnostics.push(diagnostic('svg-gradient-child-dropped', path,
                `SVG ${stop.name} inside a gradient was dropped`));
            continue;
        }
        const position = fraction(stop.attributes.offset, 0);
        const stopColor = color(stop.attributes['stop-color'] ?? '#000000');
        if (!/^#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?$/.test(stopColor) &&
            !/^\{[A-Za-z0-9_.-]+\}$/.test(stopColor) ||
            position < 0 || position > 1 || position <= (stops.at(-1)?.position ?? -1)) {
            diagnostics.push(diagnostic('svg-gradient-stop-dropped', path,
                'SVG gradient stop cannot be represented'));
            return null;
        }
        stops.push({ position, color: stopColor });
    }
    if (stops.length < 2 || stops.length > 64) {
        diagnostics.push(diagnostic('svg-gradient-stops-dropped', path,
            'SVG gradient needs between two and 64 stops'));
        return null;
    }
    const output = { kind: definition.name === 'linearGradient' ? 'linear' : 'radial',
        spread: definition.attributes.spreadMethod ?? 'pad', stops };
    if (!['pad', 'repeat', 'reflect'].includes(output.spread)) {
        diagnostics.push(diagnostic('svg-gradient-spread-dropped', path,
            'SVG gradient spread method is unsupported'));
        return null;
    }
    if (definition.name === 'linearGradient') {
        const dx = fraction(definition.attributes.x2, 1) - fraction(definition.attributes.x1, 0);
        const dy = fraction(definition.attributes.y2, 0) - fraction(definition.attributes.y1, 0);
        output.angle = (Math.atan2(dx, -dy) * 180 / Math.PI + 360) % 360;
    } else {
        output.centerX = fraction(definition.attributes.cx, 0.5);
        output.centerY = fraction(definition.attributes.cy, 0.5);
        output.radiusX = fraction(definition.attributes.r, 0.5);
        output.radiusY = output.radiusX;
        output.focalX = fraction(definition.attributes.fx, output.centerX);
        output.focalY = fraction(definition.attributes.fy, output.centerY);
    }
    return output;
}

function attributes(node, path, diagnostics) {
    const result = {};
    for (const [name, value] of Object.entries(node.attributes)) {
        if (name === 'id' || name === 'd' || name === 'points' || name === 'href' ||
            name === 'xlink:href' || name === 'xmlns' || name === 'viewBox' ||
            ['x', 'y', 'width', 'height', 'cx', 'cy', 'r', 'rx', 'ry', 'x1', 'y1', 'x2', 'y2'].includes(name)) continue;
        if (PRESENTATION.has(name)) result[name] = value;
        else diagnostics.push(diagnostic('svg-attribute-dropped', path, `SVG attribute ${name} was dropped`));
    }
    return result;
}

function idFor(node, path) {
    return node.attributes.id ?? path;
}

/** Lower a bounded SVG to source nodes accepted by normalizeImport.
 * Unsupported features carry an explicit sanitizer loss. A compiled resource
 * is returned when a caller's node or path-data budget is exceeded.
 */
export function lowerSvgAsset(svg, options = {}) {
    const nodeBudget = options.nodeBudget ?? DEFAULT_NODE_BUDGET;
    const pathDataBudget = options.pathDataBudget ?? DEFAULT_PATH_DATA_BUDGET;
    if (!Number.isSafeInteger(nodeBudget) || nodeBudget < 1 ||
        !Number.isSafeInteger(pathDataBudget) || pathDataBudget < 1) {
        throw new TypeError('SVG budgets must be positive integers');
    }
    const xml = parseXml(svg);
    const definitions = new Map();
    const diagnostics = [];
    const collect = (node) => {
        if (node.attributes.id) {
            if (definitions.has(node.attributes.id)) throw new TypeError('duplicate SVG id');
            definitions.set(node.attributes.id, node);
        }
        node.children.forEach(collect);
    };
    collect(xml);
    let count = 0;
    let pathBytes = 0;
    let budgetExceeded = false;
    const lower = (node, path, active = new Set(), instance = false,
        inherited = {}, maskContent = false) => {
        if (node.name === 'defs' || node.name === 'symbol' || DEFINITIONS.has(node.name)) return null;
        if (node.name === 'use') {
            const href = node.attributes.href ?? node.attributes['xlink:href'];
            if (!href?.startsWith('#') || !definitions.has(href.slice(1))) {
                diagnostics.push(diagnostic('svg-use-unresolved', path, 'SVG use target was not found'));
                return null;
            }
            const key = href.slice(1);
            if (active.has(key)) throw new TypeError('cyclic SVG use reference');
            return lower(definitions.get(key), `${path}/use-${key}`,
                new Set([...active, key]), true, inherited, maskContent);
        }
        if (!CONTAINERS.has(node.name) && !SHAPES.has(node.name)) {
            diagnostics.push(diagnostic('svg-element-dropped', path, `SVG ${node.name} was dropped`));
            return null;
        }
        count += 1;
        if (count > nodeBudget) budgetExceeded = true;
        const sourceId = instance ? path : idFor(node, path);
        const attributesAtNode = attributes(node, path, diagnostics);
        const paint = { ...inherited };
        for (const name of ['fill', 'stroke', 'fill-rule', 'stroke-width', 'stroke-linecap',
            'stroke-linejoin', 'stroke-miterlimit', 'stroke-dasharray', 'stroke-dashoffset']) {
            if (attributesAtNode[name] !== undefined) paint[name] = attributesAtNode[name];
        }
        if (attributesAtNode.opacity !== undefined) paint.opacity = attributesAtNode.opacity;
        if (attributesAtNode.transform !== undefined) paint.transform = attributesAtNode.transform;
        const clip = attributesAtNode['clip-path'];
        if (clip !== undefined) {
            const key = /^url\(#([A-Za-z_][\w.-]*)\)$/.exec(clip)?.[1];
            const definition = definitions.get(key);
            if (definition?.name === 'clipPath' && definition.children.length === 1 &&
                SHAPES.has(definition.children[0].name)) {
                paint.clipPath = shapePath(definition.children[0]);
            } else diagnostics.push(diagnostic('svg-clip-dropped', path,
                'SVG clipPath could not be lowered to one path'));
        }
        const mask = maskContent ? undefined : attributesAtNode.mask;
        for (const name of ['fill', 'stroke']) {
            const match = /^url\(#([A-Za-z_][\w.-]*)\)$/.exec(paint[name] ?? '');
            if (match) {
                const value = gradient(definitions.get(match[1]), path, diagnostics);
                if (value) paint[name] = value;
                else {
                    diagnostics.push(diagnostic('svg-gradient-dropped', path,
                        `SVG gradient ${match[1]} was dropped`));
                    paint[name] = 'none';
                }
            }
        }
        const svgProperties = {};
        for (const [name, value] of Object.entries(paint)) {
            if (name === 'stroke-width') svgProperties.strokeWidth = finite(value);
            else if (name === 'fill-rule') svgProperties.fillRule = value;
            else if (name === 'stroke-linecap') svgProperties.strokeLineCap = value;
            else if (name === 'stroke-linejoin') svgProperties.strokeLineJoin = value;
            else if (name === 'stroke-miterlimit') svgProperties.strokeMiterLimit = finite(value);
            else if (name === 'stroke-dashoffset') svgProperties.strokeDashoffset = finite(value);
            else if (name === 'stroke-dasharray') svgProperties.strokeDasharray = value.split(/[\s,]+/).map((part) => finite(part));
            else svgProperties[name] = value;
        }
        const output = { kind: SHAPES.has(node.name) ? 'path' : 'view', sourceId,
            properties: { svg: svgProperties }, children: [] };
        if (SHAPES.has(node.name)) {
            const d = shapePath(node);
            pathBytes += new TextEncoder().encode(d).length;
            if (pathBytes > pathDataBudget) budgetExceeded = true;
            output.properties.svg.d = d;
        }
        const childInheritance = { ...inherited };
        for (const name of ['fill', 'stroke', 'fill-rule', 'stroke-width', 'stroke-linecap',
            'stroke-linejoin', 'stroke-miterlimit', 'stroke-dasharray', 'stroke-dashoffset']) {
            if (attributesAtNode[name] !== undefined) childInheritance[name] = attributesAtNode[name];
        }
        node.children.forEach((child, index) => {
            const lowered = lower(child, `${path}/${index}`, active, instance,
                childInheritance, maskContent);
            if (lowered) output.children.push(lowered);
        });
        if (mask !== undefined) {
            const key = /^url\(#([A-Za-z_][\w.-]*)\)$/.exec(mask)?.[1];
            const definition = definitions.get(key);
            if (definition?.name === 'mask') {
                const masked = { ...output, sourceId: `${sourceId}/content` };
                const maskNode = { kind: 'view', sourceId: `${sourceId}/mask`,
                    properties: { svg: { maskMode: 'alpha' } }, children: [] };
                definition.children.forEach((child, index) => {
                    const lowered = lower(child, `${path}/mask/${index}`, active,
                        true, {}, true);
                    if (lowered) maskNode.children.push(lowered);
                });
                return { kind: 'view', sourceId, properties: { svg: {} },
                    children: [masked, maskNode] };
            }
            diagnostics.push(diagnostic('svg-mask-dropped', path,
                'SVG mask reference could not be lowered'));
        }
        return output;
    };
    const root = lower(xml, 'svg');
    if (budgetExceeded) {
        if (diagnostics.length > 0) {
            throw new TypeError('SVG with unsupported features cannot become a compiled resource');
        }
        return {
            mode: 'compiled',
            root: { kind: 'compiled-path', sourceId: xml.attributes.id ?? 'svg',
                properties: { svg }, children: [] },
            diagnostics: [diagnostic('svg-compiled-budget', '$.root',
                'SVG exceeded the declared node or path-data budget and uses one compiled resource', 'lowered')],
        };
    }
    return { mode: 'subtree', root, diagnostics };
}
