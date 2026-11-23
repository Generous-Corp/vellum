// SVG asset import. A sanitized SVG becomes, in order of preference, a
// canonical subtree of path and group nodes (so token-bound colours, stable
// identities and per-node damage keep working), one compiled vector resource
// drawn by a single node, or, last, a raster image. Every feature that is
// dropped or approximated is reported as a loss diagnostic, and hostile input
// is refused before any lowering starts.
import { namespacedIdentity } from './identity.js';
import { normalizeImport } from './normalize.js';

// Every limit the importer holds an asset to. The path, dash, stop, group
// and depth limits mirror what vellum-ui and the native materializer accept,
// so the importer never emits an asset a consumer refuses.
export const SVG_ASSET_LIMITS = Object.freeze({
    maxBytes: 1024 * 1024,
    maxDepth: 64,
    maxExpandedDepth: 64,
    maxElements: 20000,
    maxAttributes: 64,
    maxExpandedNodes: 50000,
    maxExpandedGeometry: 262144,
    maxNumber: 1e6,
    maxPathDataBytes: 64 * 1024,
    maxPathCommands: 4096,
    maxPathValues: 16384,
    maxTotalPathDataBytes: 8 * 1024 * 1024,
    maxCompiledOps: 16384,
    maxCompiledDepth: 64,
    maxSubtreeDepth: 192,
    maxDashes: 64,
    maxDashSegments: 90000,
    maxGradientStops: 64,
    maxSourceIdBytes: 900,
    maxNodeBudget: 100000,
    maxMatrixEntry: 1e9,
});
export const DEFAULT_SVG_NODE_BUDGET = 512;
export const DEFAULT_SVG_PATH_DATA_BUDGET = 64 * 1024;

export class SvgAssetRefusal extends TypeError {
    constructor(reason) {
        super(`SVG asset refused: ${reason}`);
        this.name = 'SvgAssetRefusal';
        this.reason = reason;
    }
}

function refuse(reason) {
    throw new SvgAssetRefusal(reason);
}

const IDENTITY = Object.freeze([1, 0, 0, 1, 0, 0]);

function multiply(m, n) {
    return [
        m[0] * n[0] + m[2] * n[1],
        m[1] * n[0] + m[3] * n[1],
        m[0] * n[2] + m[2] * n[3],
        m[1] * n[2] + m[3] * n[3],
        m[0] * n[4] + m[2] * n[5] + m[4],
        m[1] * n[4] + m[3] * n[5] + m[5],
    ];
}

function isIdentity(m) {
    return m[0] === 1 && m[1] === 0 && m[2] === 0 && m[3] === 1 && m[4] === 0 && m[5] === 0;
}

function apply(m, x, y) {
    return [m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]];
}

// Plain decimal notation, which every path-data and number reader takes.
function plain(number) {
    const text = String(number);
    if (!/e/i.test(text)) return text;
    const digits = Math.min(100, Math.max(0, 7 - Math.floor(Math.log10(Math.abs(number)))));
    let fixed = number.toFixed(digits);
    if (!fixed.includes('.')) return fixed;
    while (fixed.endsWith('0')) fixed = fixed.slice(0, -1);
    return fixed.endsWith('.') ? fixed.slice(0, -1) : fixed;
}

// Six decimals, or seven significant digits below 0.1, so geometry a large
// transform later scales up keeps its precision. Below 1e-12 even the
// largest matrix entry allowed leaves it under a thousandth of a pixel, so
// rounding noise from trigonometry becomes zero.
function fmt(value) {
    if (Math.abs(value) < 1e-12) return '0';
    const rounded = Math.abs(value) < 0.1
        ? Number(value.toPrecision(7))
        : Math.round(value * 1e6) / 1e6;
    return Object.is(rounded, -0) || rounded === 0 ? '0' : plain(rounded);
}

function cleanMatrix(m) {
    return m.map((value) => Number(fmt(value)));
}

// ---------------------------------------------------------------------------
// Linear scanners. Text from the asset is read by these, or by a regular
// expression with no adjacent or nested quantifiers that can match the same
// character, so no input costs more than a pass over it.

function isSpace(character) {
    return character === ' ' || character === '\t' || character === '\n' || character === '\r' ||
        character === '\f';
}

function skipSpace(text, index) {
    let at = index;
    while (at < text.length && isSpace(text[at])) at += 1;
    return at;
}

function isDigit(character) {
    return character !== undefined && character >= '0' && character <= '9';
}

// The end of the number that starts at `index`, as CSS and SVG write one:
// an optional sign, digits with an optional fraction or a fraction alone,
// then, when `exponent` allows, an optional exponent. -1 when none starts
// there.
function numberEnd(text, index, exponent = true) {
    let at = index;
    if (text[at] === '+' || text[at] === '-') at += 1;
    let digits = 0;
    while (isDigit(text[at])) { at += 1; digits += 1; }
    if (text[at] === '.') {
        at += 1;
        while (isDigit(text[at])) { at += 1; digits += 1; }
    }
    if (digits === 0) return -1;
    if (exponent && (text[at] === 'e' || text[at] === 'E')) {
        let next = at + 1;
        if (text[next] === '+' || text[next] === '-') next += 1;
        const start = next;
        while (isDigit(text[next])) next += 1;
        if (next > start) at = next;
    }
    return at;
}

// The number `text` holds exactly, followed by `suffix`, or null.
function exactNumber(text, suffix = '', exponent = true) {
    if (!text.endsWith(suffix)) return null;
    const body = text.slice(0, text.length - suffix.length);
    return body && numberEnd(body, 0, exponent) === body.length ? Number(body) : null;
}

// `url(...)` read from `index`, which holds "url(" in any case: the text it
// names and where it ends, or null when it is not closed. A quoted URL ends
// at its quote; an unquoted one at space or a parenthesis.
function readUrl(text, index) {
    let at = skipSpace(text, index + 4);
    let inner;
    if (text[at] === '"' || text[at] === "'") {
        const close = text.indexOf(text[at], at + 1);
        if (close === -1) return null;
        inner = text.slice(at + 1, close);
        at = close + 1;
    } else {
        const start = at;
        while (at < text.length && text[at] !== ')' && text[at] !== '(' && !isSpace(text[at]) &&
            text[at] !== '"' && text[at] !== "'") at += 1;
        inner = text.slice(start, at);
    }
    if (/[()"']/.test(inner)) return null;
    at = skipSpace(text, at);
    return text[at] === ')' ? { inner, end: at + 1 } : null;
}

// A value that starts with `url(...)`: the local id it names (null when it
// is not local) and the trimmed rest, or null when it is not a URL.
function leadingUrl(value) {
    const text = value.trim();
    if (text.slice(0, 4).toLowerCase() !== 'url(') return null;
    const url = readUrl(text, 0);
    if (!url) return null;
    return { id: localReference(url.inner), inner: url.inner, rest: text.slice(url.end).trim() };
}

// `!important` at the end of a declaration, and the value before it. CSS
// reads it as the token "!", optional white space, then the keyword
// "important" in any case. A comment between them never reaches here: a
// style with a comment is refused.
function splitImportant(raw) {
    const text = raw.trimEnd();
    const keyword = text.slice(-'important'.length);
    if (keyword.toLowerCase() !== 'important') return { value: text, important: false };
    let at = text.length - keyword.length;
    while (at > 0 && isSpace(text[at - 1])) at -= 1;
    if (text[at - 1] !== '!') return { value: text, important: false };
    return { value: text.slice(0, at - 1).trimEnd(), important: true };
}

// ---------------------------------------------------------------------------
// XML: a strict subset. Declarations other than the leading XML declaration,
// document types, entities, CDATA and processing instructions are refused.

const PREDEFINED_ENTITIES = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'" };

function decodeText(value) {
    return value.replace(/&([^;&\s]*);?/g, (match, name) => {
        if (!match.endsWith(';')) refuse('a bare ampersand is not well-formed XML');
        if (name.startsWith('#')) {
            const hex = /^#x[0-9a-fA-F]{1,6}$/.test(name);
            const decimal = /^#[0-9]{1,7}$/.test(name);
            if (!hex && !decimal) refuse(`malformed character reference &${name};`);
            const code = hex ? Number.parseInt(name.slice(2), 16) : Number.parseInt(name.slice(1), 10);
            if (code === 0 || code > 0x10ffff || (code >= 0xd800 && code <= 0xdfff)) {
                refuse(`invalid character reference &${name};`);
            }
            return String.fromCodePoint(code);
        }
        if (!Object.hasOwn(PREDEFINED_ENTITIES, name)) refuse(`unknown entity &${name};`);
        return PREDEFINED_ENTITIES[name];
    });
}

const TEXT_ELEMENTS = new Set(['title', 'desc', 'text', 'tspan', 'textPath', 'style', 'metadata']);
const SVG_NAMESPACE = 'http://www.w3.org/2000/svg';
const XLINK_NAMESPACE = 'http://www.w3.org/1999/xlink';
const XML_NAMESPACE = 'http://www.w3.org/XML/1998/namespace';
// Editor metadata namespaces: their elements and attributes never draw, so
// they are dropped. Anything else outside SVG, XLink and XML is refused.
const METADATA_NAMESPACES = new Set([
    'http://www.inkscape.org/namespaces/inkscape',
    'http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd',
    'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
    'http://purl.org/dc/elements/1.1/',
    'http://creativecommons.org/ns#',
    'http://www.bohemiancoding.com/sketch/ns',
    'http://www.serif.com/',
]);

function splitName(name) {
    const colon = name.indexOf(':');
    return colon === -1 ? ['', name] : [name.slice(0, colon), name.slice(colon + 1)];
}

// Resolves an element's namespace declarations and qualified names once.
// The element keeps its local name when it is in the SVG namespace; an
// attribute keeps its local name, or `xlink:` or `xml:` and its local name
// for those namespaces, whatever prefix the document used.
function resolveNames(qualified, raw, parentScope) {
    const scope = new Map(parentScope);
    for (const [name, value] of raw) {
        if (name === 'xmlns') scope.set('', value.trim());
        else if (name.startsWith('xmlns:')) {
            const prefix = name.slice(6);
            if (prefix === 'xml' || prefix === 'xmlns' || !prefix) refuse(`<${qualified}> rebinds ${name}`);
            scope.set(prefix, value.trim());
        }
    }
    const [prefix, local] = splitName(qualified);
    if (prefix && !scope.has(prefix)) refuse(`<${qualified}> uses the unbound prefix ${prefix}`);
    const namespace = scope.get(prefix);
    const attributes = Object.create(null);
    for (const [name, value] of raw) {
        if (name === 'xmlns' || name.startsWith('xmlns:')) continue;
        const [attributePrefix, attributeLocal] = splitName(name);
        let key = attributeLocal;
        if (attributePrefix === 'xml') {
            key = `xml:${attributeLocal}`;
        } else if (attributePrefix) {
            if (!scope.has(attributePrefix)) refuse(`<${qualified}> ${name} uses the unbound prefix ${attributePrefix}`);
            const attributeNamespace = scope.get(attributePrefix);
            if (attributeNamespace === XLINK_NAMESPACE) key = `xlink:${attributeLocal}`;
            else if (attributeNamespace === XML_NAMESPACE) key = `xml:${attributeLocal}`;
            else if (attributeNamespace === SVG_NAMESPACE) key = attributeLocal;
            else if (METADATA_NAMESPACES.has(attributeNamespace)) continue;
            else refuse(`<${qualified}> ${name} is in the unsupported namespace ${attributeNamespace}`);
        }
        if (Object.hasOwn(attributes, key)) refuse(`duplicate attribute ${key} on <${qualified}>`);
        attributes[key] = value;
    }
    return { namespace, local, attributes, scope };
}

// Removes XML comments with one linear scan. A comment holding "--" or one
// left open is malformed.
function stripComments(text) {
    let output = '';
    let cursor = 0;
    for (;;) {
        const start = text.indexOf('<!--', cursor);
        if (start === -1) return output + text.slice(cursor);
        const end = text.indexOf('-->', start + 4);
        if (end === -1) refuse('an unterminated comment');
        if (text.slice(start + 4, end).includes('--')) refuse('a malformed comment');
        output += text.slice(cursor, start);
        cursor = end + 3;
    }
}

const NAME_START = /^[A-Za-z_]$/;
const ATTRIBUTE_START = /^[A-Za-z_:]$/;
const NAME_CHARACTER = /^[\w.:-]$/;

function nameEndAt(text, index) {
    let at = index;
    while (at < text.length && NAME_CHARACTER.test(text[at])) at += 1;
    return at;
}

// The tag that starts at the "<" at `index`: closing or not, its name, its
// attributes with their raw values, whether it closes itself and where it
// ends; null when it is malformed. Attributes are separated by space and
// their values quoted, without "<".
function scanTag(text, index) {
    let at = index + 1;
    const closing = text[at] === '/';
    if (closing) at += 1;
    if (!NAME_START.test(text[at] ?? '')) return null;
    const nameEnd = nameEndAt(text, at + 1);
    const name = text.slice(at, nameEnd);
    at = nameEnd;
    const attributes = [];
    for (;;) {
        const spaced = skipSpace(text, at);
        if (text[spaced] === '>') return { closing, name, attributes, selfClosing: false, end: spaced + 1 };
        if (text[spaced] === '/' && text[spaced + 1] === '>') {
            return { closing, name, attributes, selfClosing: true, end: spaced + 2 };
        }
        if (spaced === at || !ATTRIBUTE_START.test(text[spaced] ?? '')) return null;
        const attributeEnd = nameEndAt(text, spaced + 1);
        const attribute = text.slice(spaced, attributeEnd);
        at = skipSpace(text, attributeEnd);
        if (text[at] !== '=') return null;
        at = skipSpace(text, at + 1);
        const quote = text[at];
        if (quote !== '"' && quote !== "'") return null;
        const close = text.indexOf(quote, at + 1);
        if (close === -1) return null;
        const value = text.slice(at + 1, close);
        if (value.includes('<')) return null;
        attributes.push([attribute, value]);
        at = close + 1;
    }
}

function parseXml(svg) {
    if (typeof svg !== 'string') refuse('the asset is not a string');
    if (utf8Length(svg) > SVG_ASSET_LIMITS.maxBytes) {
        refuse(`the asset exceeds ${SVG_ASSET_LIMITS.maxBytes} bytes`);
    }
    let text = svg.replace(/^﻿/, '');
    const start = skipSpace(text, 0);
    if (text.startsWith('<?xml', start) && isSpace(text[start + 5])) {
        const close = text.indexOf('?', start + 5);
        if (close !== -1 && text[close + 1] === '>') text = text.slice(close + 2);
    }
    if (/<!DOCTYPE/i.test(text)) refuse('a document type declaration is not allowed');
    if (/<!ENTITY/i.test(text)) refuse('an entity declaration is not allowed');
    if (/<!\[CDATA\[/i.test(text)) refuse('a CDATA section is not allowed');
    if (/<\?/.test(text)) refuse('a processing instruction is not allowed');
    text = stripComments(text);
    if (/<!/.test(text)) refuse('a markup declaration is not allowed');

    const root = { name: '#document', qualified: '#document', attributes: Object.create(null),
        children: [], text: '', dropped: false,
        scope: new Map([['', SVG_NAMESPACE], ['xml', XML_NAMESPACE]]) };
    const stack = [root];
    let elements = 0;
    let cursor = 0;
    while (cursor < text.length) {
        const next = text.indexOf('<', cursor);
        const content = text.slice(cursor, next === -1 ? text.length : next);
        if (content.trim()) {
            const parent = stack.at(-1);
            if (parent.dropped) {
                decodeText(content);
            } else {
                if (!TEXT_ELEMENTS.has(parent.name)) refuse(`text content inside <${parent.name}>`);
                parent.text += decodeText(content);
            }
        }
        if (next === -1) break;
        const scanned = scanTag(text, next);
        if (!scanned) refuse(`malformed markup at byte ${next}`);
        cursor = scanned.end;
        const { closing, name, attributes: found, selfClosing } = scanned;
        if (closing) {
            if (found.length > 0 || selfClosing) refuse(`malformed closing tag </${name}>`);
            if (stack.length < 2 || stack.at(-1).qualified !== name) {
                refuse(`mismatched closing tag </${name}>`);
            }
            stack.pop();
            continue;
        }
        if (++elements > SVG_ASSET_LIMITS.maxElements) {
            refuse(`the asset has more than ${SVG_ASSET_LIMITS.maxElements} elements`);
        }
        const raw = [];
        const seen = new Set();
        for (const [attribute, value] of found) {
            if (raw.length >= SVG_ASSET_LIMITS.maxAttributes) {
                refuse(`<${name}> has more than ${SVG_ASSET_LIMITS.maxAttributes} attributes`);
            }
            if (seen.has(attribute)) refuse(`duplicate attribute ${attribute} on <${name}>`);
            seen.add(attribute);
            raw.push([attribute, decodeText(value)]);
        }
        const parent = stack.at(-1);
        const resolved = resolveNames(name, raw, parent.scope);
        const dropped = parent.dropped || METADATA_NAMESPACES.has(resolved.namespace);
        if (!dropped && resolved.namespace !== SVG_NAMESPACE) {
            refuse(`<${name}> is outside the SVG namespace (${resolved.namespace || 'none'})`);
        }
        const node = { name: resolved.local, qualified: name, attributes: resolved.attributes,
            children: [], text: '', dropped, scope: resolved.scope };
        Object.defineProperty(node, 'parent', { value: parent });
        if (!dropped) parent.children.push(node);
        if (!selfClosing) {
            stack.push(node);
            if (stack.length - 1 > SVG_ASSET_LIMITS.maxDepth) {
                refuse(`elements nest deeper than ${SVG_ASSET_LIMITS.maxDepth}`);
            }
        }
    }
    if (stack.length !== 1) refuse(`unclosed element <${stack.at(-1).name}>`);
    if (root.children.length !== 1 || root.children[0].name !== 'svg') {
        refuse('the document must have exactly one <svg> root element');
    }
    return root.children[0];
}

// ---------------------------------------------------------------------------
// Hostile-input checks, all run before lowering starts.

const FORBIDDEN_ELEMENTS = new Set([
    'script', 'foreignObject', 'iframe', 'object', 'embed', 'audio', 'video', 'canvas',
    'handler', 'listener', 'html', 'body',
]);
const COLOR_ATTRIBUTES = new Set(['fill', 'stroke', 'stop-color', 'color', 'flood-color',
    'lighting-color']);
const NON_NUMERIC_ATTRIBUTES = new Set(['id', 'class', 'href', 'xlink:href', 'xmlns',
    'xmlns:xlink', 'version', 'baseProfile', 'preserveAspectRatio', 'style', 'xml:space',
    'font-family', 'clip-path', 'mask', 'filter', 'marker-start', 'marker-mid', 'marker-end',
    'gradientUnits', 'spreadMethod', 'clipPathUnits', 'maskUnits', 'maskContentUnits',
    'mask-type', 'fill-rule', 'clip-rule', 'stroke-linecap', 'stroke-linejoin', 'display',
    'visibility', 'overflow', 'color-interpolation', 'shape-rendering', 'vector-effect',
    'paint-order', 'mix-blend-mode', 'isolation', 'xml:lang', 'lang', 'role',
    'aria-label', 'patternUnits', 'patternContentUnits', 'filterUnits', 'primitiveUnits',
    'text-anchor', 'dominant-baseline', 'font-weight', 'font-style', 'pointer-events',
    'cursor', 'title']);
function checkNumbers(value, where) {
    let at = 0;
    while (at < value.length) {
        const end = numberEnd(value, at);
        if (end === -1) {
            at += 1;
            continue;
        }
        const text = value.slice(at, end);
        const number = Number(text);
        if (!Number.isFinite(number) || Math.abs(number) > SVG_ASSET_LIMITS.maxNumber) {
            refuse(`${where} has the number ${text.slice(0, 32)}, beyond ±${SVG_ASSET_LIMITS.maxNumber}`);
        }
        at = end;
    }
}

function localReference(value) {
    const text = value.trim();
    if (text[0] !== '#' || text.length < 2) return null;
    const id = text.slice(1);
    return /[\s#]/.test(id) ? null : id;
}

// The local ids every url() in a value names; any other URL, or one left
// open, is refused.
function urlReferences(value, where) {
    const references = [];
    const lower = value.toLowerCase();
    let from = 0;
    for (;;) {
        const start = lower.indexOf('url(', from);
        if (start === -1) return references;
        const url = readUrl(value, start);
        if (!url) refuse(`${where} has a malformed url()`);
        const id = localReference(url.inner);
        if (id === null) refuse(`${where} references ${url.inner.slice(0, 64) || 'an empty URL'} outside the asset`);
        references.push(id);
        from = url.end;
    }
}

// CSS the importer does not model, any of which could carry a resource:
// at-rules, comments, escapes and image functions.
const UNMODELLED_CSS = ['@', '/*', '\\', 'expression(', 'javascript:', 'image-set', 'image(',
    'cross-fade', 'element('];

function unmodelledCss(value) {
    const compact = value.toLowerCase().replace(/\s+/g, '');
    return UNMODELLED_CSS.some((part) => compact.includes(part));
}

function styleDeclarations(value, where) {
    if (unmodelledCss(value)) {
        refuse(`${where} has a style with an at-rule, comment, escape, expression or image function`);
    }
    const declarations = Object.create(null);
    const important = new Set();
    for (const part of value.split(';')) {
        if (!part.trim()) continue;
        const colon = part.indexOf(':');
        if (colon <= 0) refuse(`${where} has a malformed style declaration`);
        const name = part.slice(0, colon).trim().toLowerCase();
        const raw = splitImportant(part.slice(colon + 1).trim());
        if (!/^[a-z-]+$/.test(name)) refuse(`${where} has a malformed style property`);
        // A later normal declaration does not override an important one.
        if (important.has(name) && !raw.important) continue;
        if (raw.important) important.add(name);
        declarations[name] = raw.value;
    }
    return declarations;
}

// Animation elements that can change another attribute's value. They are
// dropped from the lowered asset, but one that could point a reference
// outside the asset, by targeting href or by animating to a URL, is refused.
const ANIMATING_ELEMENTS = new Set(['animate', 'animateColor', 'animateMotion',
    'animateTransform', 'set', 'discard']);
// A scheme, a protocol-relative path or a url(), as anything an animation
// could point outside the asset with.
function urlLike(value) {
    return value.includes(':') || value.includes('//') || value.toLowerCase().includes('url(');
}

function checkAnimation(node, where) {
    const target = (node.attributes.attributeName ?? '').trim().toLowerCase();
    if (target === 'href' || target.endsWith(':href') || target === 'src') {
        refuse(`${where} animates the reference ${node.attributes.attributeName}`);
    }
    for (const name of ['to', 'from', 'by', 'values']) {
        const value = node.attributes[name];
        if (value === undefined) continue;
        for (const item of value.split(';')) {
            const url = leadingUrl(item);
            if (urlLike(item) && !(url && url.id !== null && url.rest === '')) {
                refuse(`${where} ${name} animates to a reference outside the asset`);
            }
        }
    }
}

// Following one of these references copies the referenced element's content
// into the referencing one, so it counts toward the expansion budget.
const EXPANDING_PROPERTIES = new Set(['clip-path', 'mask', 'filter', 'marker-start', 'marker-mid',
    'marker-end']);
const TEMPLATES = new Set(['pattern', 'filter']);

// Every reference an element holds, as [target, expands]. A use, clip,
// mask, filter, marker, pattern paint or pattern or filter template copies
// its target; a gradient paint or gradient template only reads stops.
function referencesOf(node, ids) {
    const found = [];
    for (const name of ['href', 'xlink:href']) {
        const target = ids.get(localReference(node.attributes[name] ?? '') ?? '');
        if (target) found.push([target, node.name === 'use' || TEMPLATES.has(target.name)]);
    }
    const values = [...Object.entries(node.attributes)];
    if (node.attributes.style !== undefined) {
        values.push(...Object.entries(styleDeclarations(node.attributes.style, `<${node.name}>`)));
    }
    for (const [name, value] of values) {
        if (name === 'href' || name === 'xlink:href' || name === 'style') continue;
        for (const id of urlReferences(value, `<${node.name}> ${name}`)) {
            const target = ids.get(id);
            if (!target) continue;
            found.push([target, EXPANDING_PROPERTIES.has(name) ||
                ((name === 'fill' || name === 'stroke') && target.name === 'pattern')]);
        }
    }
    return found;
}

// One iterative walk over the document's children and references refuses a
// reference cycle and bounds what expansion can cost before any is done: the
// nodes every reference kind copies in and how deep they nest, each element
// costed once.
// The geometry an element emits each time it is drawn: a path's commands
// and coordinate pairs, or a polyline's points, counted in one pass without
// parsing.
function geometryWeight(node) {
    const data = node.name === 'path' ? node.attributes.d
        : node.name === 'polyline' || node.name === 'polygon' ? node.attributes.points : undefined;
    if (typeof data !== 'string') return 0;
    let commands = 0;
    let numbers = 0;
    let at = 0;
    while (at < data.length) {
        const end = numberEnd(data, at);
        if (end !== -1) {
            numbers += 1;
            at = end;
            continue;
        }
        if (/[A-Za-z]/.test(data[at])) commands += 1;
        at += 1;
    }
    return commands + Math.ceil(numbers / 2);
}

function checkExpansion(root, ids) {
    const sizes = new Map();
    const geometry = new Map();
    const depths = new Map();
    const open = new Set();
    const edgesOf = (node) => [...node.children.map((child) => [child, true]),
        ...referencesOf(node, ids)];
    const frames = [[root, edgesOf(root), 0]];
    open.add(root);
    while (frames.length > 0) {
        const frame = frames.at(-1);
        const [node, edges] = frame;
        if (frame[2] < edges.length) {
            const [target] = edges[frame[2]];
            frame[2] += 1;
            if (open.has(target)) {
                const trail = frames.slice(frames.findIndex(([held]) => held === target))
                    .map(([held]) => held.attributes.id ?? `<${held.name}>`);
                refuse(`the reference cycle ${[...trail, trail[0]].slice(0, 16).join(' -> ')}`);
            }
            if (sizes.has(target)) continue;
            open.add(target);
            frames.push([target, edgesOf(target), 0]);
            continue;
        }
        let total = 1;
        let weight = geometryWeight(node);
        let depth = 1;
        for (const [target, expands] of edges) {
            if (!expands) continue;
            total += sizes.get(target);
            weight += geometry.get(target);
            depth = Math.max(depth, 1 + depths.get(target));
        }
        sizes.set(node, Math.min(total, SVG_ASSET_LIMITS.maxExpandedNodes + 1));
        geometry.set(node, Math.min(weight, SVG_ASSET_LIMITS.maxExpandedGeometry + 1));
        depths.set(node, Math.min(depth, SVG_ASSET_LIMITS.maxExpandedDepth + 1));
        open.delete(node);
        frames.pop();
    }
    if (sizes.get(root) > SVG_ASSET_LIMITS.maxExpandedNodes) {
        refuse(`references expand to more than ${SVG_ASSET_LIMITS.maxExpandedNodes} nodes`);
    }
    if (geometry.get(root) > SVG_ASSET_LIMITS.maxExpandedGeometry) {
        refuse(`references expand to more than ${SVG_ASSET_LIMITS.maxExpandedGeometry} path commands and points`);
    }
    if (depths.get(root) > SVG_ASSET_LIMITS.maxExpandedDepth) {
        refuse(`references nest deeper than ${SVG_ASSET_LIMITS.maxExpandedDepth}`);
    }
}

function checkHostility(root) {
    const ids = new Map();
    const visit = (node) => {
        const where = `<${node.name}>`;
        if (FORBIDDEN_ELEMENTS.has(node.name)) refuse(`${where} is not allowed in an asset`);
        // A CSS escape (\69 for "i") can spell any forbidden word, so a
        // <style> element may not contain one at all.
        if (node.name === 'style' && /@import|url\(|expression|javascript:|\\/i.test(node.text)) {
            refuse('a <style> element with an import, URL, expression or escape');
        }
        if (ANIMATING_ELEMENTS.has(node.name)) checkAnimation(node, where);
        for (const [name, value] of Object.entries(node.attributes)) {
            const lower = name.toLowerCase();
            if (lower.startsWith('on')) refuse(`${where} has the event handler ${name}`);
            if (lower === 'xml:base') refuse(`${where} sets xml:base`);
            // No SVG attribute needs a CSS escape, and one can spell a URL.
            if (value.includes('\\')) refuse(`${where} ${name} holds a CSS escape`);
            if (lower === 'href' || lower === 'xlink:href') {
                const id = localReference(value);
                if (id === null) refuse(`${where} references ${value} outside the asset`);
                continue;
            }
            if (/javascript:|data:/i.test(value)) refuse(`${where} ${name} holds a script or data URL`);
            urlReferences(value, `${where} ${name}`);
            if (name === 'style') {
                for (const [property, declared] of Object.entries(styleDeclarations(value, where))) {
                    urlReferences(declared, `${where} style ${property}`);
                    if (!COLOR_ATTRIBUTES.has(property)) checkNumbers(declared, `${where} style ${property}`);
                }
                continue;
            }
            if (!COLOR_ATTRIBUTES.has(name) && !NON_NUMERIC_ATTRIBUTES.has(name)) {
                checkNumbers(value, `${where} ${name}`);
            }
        }
        if (typeof node.attributes.id === 'string') {
            const id = node.attributes.id;
            if (!id || id.length > 256) refuse(`${where} has an empty or overlong id`);
            if (!/^[\p{L}\p{N}_.:-]+$/u.test(id)) refuse(`${where} has an id with characters outside letters, digits and _.:-`);
            if (ids.has(id)) refuse(`the id ${id} is used twice`);
            ids.set(id, node);
        }
        for (const child of node.children) visit(child);
    };
    visit(root);
    checkExpansion(root, ids);
    return ids;
}

// ---------------------------------------------------------------------------
// Values.

const NAMED_COLORS = (() => {
    const table = 'aliceblue f0f8ff antiquewhite faebd7 aqua 00ffff aquamarine 7fffd4 azure f0ffff beige f5f5dc bisque ffe4c4 black 000000 blanchedalmond ffebcd blue 0000ff blueviolet 8a2be2 brown a52a2a burlywood deb887 cadetblue 5f9ea0 chartreuse 7fff00 chocolate d2691e coral ff7f50 cornflowerblue 6495ed cornsilk fff8dc crimson dc143c cyan 00ffff darkblue 00008b darkcyan 008b8b darkgoldenrod b8860b darkgray a9a9a9 darkgreen 006400 darkgrey a9a9a9 darkkhaki bdb76b darkmagenta 8b008b darkolivegreen 556b2f darkorange ff8c00 darkorchid 9932cc darkred 8b0000 darksalmon e9967a darkseagreen 8fbc8f darkslateblue 483d8b darkslategray 2f4f4f darkslategrey 2f4f4f darkturquoise 00ced1 darkviolet 9400d3 deeppink ff1493 deepskyblue 00bfff dimgray 696969 dimgrey 696969 dodgerblue 1e90ff firebrick b22222 floralwhite fffaf0 forestgreen 228b22 fuchsia ff00ff gainsboro dcdcdc ghostwhite f8f8ff gold ffd700 goldenrod daa520 gray 808080 green 008000 greenyellow adff2f grey 808080 honeydew f0fff0 hotpink ff69b4 indianred cd5c5c indigo 4b0082 ivory fffff0 khaki f0e68c lavender e6e6fa lavenderblush fff0f5 lawngreen 7cfc00 lemonchiffon fffacd lightblue add8e6 lightcoral f08080 lightcyan e0ffff lightgoldenrodyellow fafad2 lightgray d3d3d3 lightgreen 90ee90 lightgrey d3d3d3 lightpink ffb6c1 lightsalmon ffa07a lightseagreen 20b2aa lightskyblue 87cefa lightslategray 778899 lightslategrey 778899 lightsteelblue b0c4de lightyellow ffffe0 lime 00ff00 limegreen 32cd32 linen faf0e6 magenta ff00ff maroon 800000 mediumaquamarine 66cdaa mediumblue 0000cd mediumorchid ba55d3 mediumpurple 9370db mediumseagreen 3cb371 mediumslateblue 7b68ee mediumspringgreen 00fa9a mediumturquoise 48d1cc mediumvioletred c71585 midnightblue 191970 mintcream f5fffa mistyrose ffe4e1 moccasin ffe4b5 navajowhite ffdead navy 000080 oldlace fdf5e6 olive 808000 olivedrab 6b8e23 orange ffa500 orangered ff4500 orchid da70d6 palegoldenrod eee8aa palegreen 98fb98 paleturquoise afeeee palevioletred db7093 papayawhip ffefd5 peachpuff ffdab9 peru cd853f pink ffc0cb plum dda0dd powderblue b0e0e6 purple 800080 rebeccapurple 663399 red ff0000 rosybrown bc8f8f royalblue 4169e1 saddlebrown 8b4513 salmon fa8072 sandybrown f4a460 seagreen 2e8b57 seashell fff5ee sienna a0522d silver c0c0c0 skyblue 87ceeb slateblue 6a5acd slategray 708090 slategrey 708090 snow fffafa springgreen 00ff7f steelblue 4682b4 tan d2b48c teal 008080 thistle d8bfd8 tomato ff6347 turquoise 40e0d0 violet ee82ee wheat f5deb3 white ffffff whitesmoke f5f5f5 yellow ffff00 yellowgreen 9acd32'.split(' ');
    const colors = new Map();
    for (let index = 0; index < table.length; index += 2) colors.set(table[index], table[index + 1]);
    return colors;
})();

// An RGBA colour with 0-255 channels and a 0-1 alpha, or null when the
// value is not a colour this importer reads.
function parseColor(value) {
    const text = value.trim().toLowerCase();
    if (text === 'transparent') return [0, 0, 0, 0];
    if (NAMED_COLORS.has(text)) return hexColor(NAMED_COLORS.get(text));
    if (/^#[0-9a-f]{3,4}$/.test(text)) {
        return hexColor([...text.slice(1)].map((digit) => digit + digit).join(''));
    }
    if (/^#[0-9a-f]{6}(?:[0-9a-f]{2})?$/.test(text)) return hexColor(text.slice(1));
    const open = text.indexOf('(');
    const kind = text.slice(0, open);
    if (!['rgb', 'rgba', 'hsl', 'hsla'].includes(kind) || !text.endsWith(')')) return null;
    const body = text.slice(open + 1, -1);
    if (body.includes(')') || body.includes('(')) return null;
    const functional = [text, kind];
    const parts = body.split(/[\s,/]+/).filter(Boolean);
    if (parts.length !== 3 && parts.length !== 4) return null;
    const alpha = parts.length === 4 ? channel(parts[3], 1) : 1;
    if (alpha === null) return null;
    if (functional[1].startsWith('rgb')) {
        const rgb = parts.slice(0, 3).map((part) => channel(part, 255));
        if (rgb.some((part) => part === null)) return null;
        return [...rgb.map((part) => Math.round(Math.min(255, Math.max(0, part)))),
            Math.min(1, Math.max(0, alpha))];
    }
    const turns = { deg: 1, rad: 180 / Math.PI, grad: 0.9, turn: 360 };
    const unitStart = numberEnd(parts[0], 0, false);
    const unit = unitStart === -1 ? null : parts[0].slice(unitStart) || 'deg';
    if (unit === null || !Object.hasOwn(turns, unit)) return null;
    const hue = Number(parts[0].slice(0, unitStart)) * turns[unit];
    let saturation = channel(parts[1], 1);
    let lightness = channel(parts[2], 1);
    if (!Number.isFinite(hue) || saturation === null || lightness === null ||
        !parts[1].endsWith('%') || !parts[2].endsWith('%')) return null;
    saturation = Math.min(1, Math.max(0, saturation));
    lightness = Math.min(1, Math.max(0, lightness));
    const h = ((hue % 360) + 360) % 360 / 360;
    const q = lightness < 0.5 ? lightness * (1 + saturation) : lightness + saturation - lightness * saturation;
    const p = 2 * lightness - q;
    const component = (t) => {
        const wrapped = (t % 1 + 1) % 1;
        if (wrapped < 1 / 6) return p + (q - p) * 6 * wrapped;
        if (wrapped < 1 / 2) return q;
        if (wrapped < 2 / 3) return p + (q - p) * (2 / 3 - wrapped) * 6;
        return p;
    };
    return [component(h + 1 / 3), component(h), component(h - 1 / 3)]
        .map((part) => Math.round(part * 255)).concat(Math.min(1, Math.max(0, alpha)));
}

function channel(part, scale) {
    const percent = part.endsWith('%') ? exactNumber(part, '%', false) : null;
    if (percent !== null) return percent / 100 * scale;
    return exactNumber(part, '', false);
}

function hexColor(hex) {
    const values = [0, 2, 4].map((offset) => Number.parseInt(hex.slice(offset, offset + 2), 16));
    return [...values, hex.length === 8 ? Number.parseInt(hex.slice(6, 8), 16) / 255 : 1];
}

function hexString(color, opacity = 1) {
    const alpha = Math.round(Math.min(1, Math.max(0, color[3] * opacity)) * 255);
    const hex = color.slice(0, 3).map((part) =>
        Math.round(Math.min(255, Math.max(0, part))).toString(16).padStart(2, '0')).join('');
    return alpha === 255 ? `#${hex}` : `#${hex}${alpha.toString(16).padStart(2, '0')}`;
}

function parseNumber(value) {
    if (value === undefined) return undefined;
    return exactNumber(value.trim());
}

// A length in user units: a number, optionally in px. Other units are not
// read, so the caller reports the attribute and uses its default.
function parseLength(value) {
    if (value === undefined) return undefined;
    const text = value.trim();
    return text.endsWith('px') ? exactNumber(text, 'px') : exactNumber(text);
}

function parseFraction(value, fallback) {
    if (value === undefined) return fallback;
    const text = value.trim();
    const percent = text.endsWith('%') ? exactNumber(text, '%') : null;
    if (percent !== null) return percent / 100;
    const number = parseNumber(text);
    return number === null ? null : number;
}

function parseTransform(value) {
    let matrix = [...IDENTITY];
    const text = value.trim();
    if (!text) return matrix;
    const pattern = /\s*(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)\s*,?/gy;
    let consumed = 0;
    for (const match of text.matchAll(pattern)) {
        consumed = match.index + match[0].length;
        const args = match[2].trim().split(/[\s,]+/).filter(Boolean).map(Number);
        if (args.some((number) => !Number.isFinite(number))) return null;
        const [a, b, c] = args;
        let step;
        switch (match[1]) {
        case 'matrix':
            if (args.length !== 6) return null;
            step = args;
            break;
        case 'translate':
            if (args.length < 1 || args.length > 2) return null;
            step = [1, 0, 0, 1, a, b ?? 0];
            break;
        case 'scale':
            if (args.length < 1 || args.length > 2) return null;
            step = [a, 0, 0, b ?? a, 0, 0];
            break;
        case 'rotate': {
            if (args.length !== 1 && args.length !== 3) return null;
            const radians = a * Math.PI / 180;
            const cos = Math.cos(radians);
            const sin = Math.sin(radians);
            step = [cos, sin, -sin, cos, 0, 0];
            if (args.length === 3) {
                step = multiply(multiply([1, 0, 0, 1, b, c], step), [1, 0, 0, 1, -b, -c]);
            }
            break;
        }
        case 'skewX':
            if (args.length !== 1) return null;
            step = [1, 0, Math.tan(a * Math.PI / 180), 1, 0, 0];
            break;
        default:
            if (args.length !== 1) return null;
            step = [1, Math.tan(a * Math.PI / 180), 0, 1, 0, 0];
        }
        matrix = multiply(matrix, step);
    }
    if (consumed !== text.length) return null;
    return matrix.every((value) => Number.isFinite(value) &&
        Math.abs(value) <= SVG_ASSET_LIMITS.maxNumber) ? matrix : null;
}

// ---------------------------------------------------------------------------
// Path data: normalized to absolute M, L, C, Q, A and Z segments.

function parsePathData(data) {
    const segments = [];
    const text = data.trim();
    let index = 0;
    const skip = () => {
        while (index < text.length && /[\s,]/.test(text[index])) index += 1;
    };
    const numberAt = () => {
        skip();
        const end = numberEnd(text, index);
        if (end === -1) return null;
        const number = Number(text.slice(index, end));
        index = end;
        return number;
    };
    const flagAt = () => {
        skip();
        const flag = text[index];
        if (flag !== '0' && flag !== '1') return null;
        index += 1;
        return Number(flag);
    };
    let x = 0;
    let y = 0;
    let startX = 0;
    let startY = 0;
    let lastControl = null;
    let lastQuadratic = null;
    let command = null;
    const arity = { M: 2, L: 2, H: 1, V: 1, C: 6, S: 4, Q: 4, T: 2, A: 7, Z: 0 };
    while (true) {
        skip();
        if (index >= text.length) break;
        if (/[MmLlHhVvCcSsQqTtAaZz]/.test(text[index])) {
            command = text[index];
            index += 1;
        } else if (command === null || command === 'Z' || command === 'z') {
            return null;
        }
        const upper = command.toUpperCase();
        const relative = command !== upper;
        if (segments.length === 0 && upper !== 'M') return null;
        if (upper === 'Z') {
            segments.push(['Z']);
            x = startX;
            y = startY;
            lastControl = null;
            lastQuadratic = null;
            continue;
        }
        const values = [];
        for (let item = 0; item < arity[upper]; item += 1) {
            const value = upper === 'A' && (item === 3 || item === 4) ? flagAt() : numberAt();
            if (value === null) return null;
            values.push(value);
        }
        const ox = relative ? x : 0;
        const oy = relative ? y : 0;
        switch (upper) {
        case 'M':
            x = values[0] + ox;
            y = values[1] + oy;
            startX = x;
            startY = y;
            segments.push(['M', x, y]);
            command = relative ? 'l' : 'L';
            lastControl = null;
            lastQuadratic = null;
            break;
        case 'L':
        case 'H':
        case 'V':
            if (upper === 'L') {
                x = values[0] + ox;
                y = values[1] + oy;
            } else if (upper === 'H') {
                x = values[0] + ox;
            } else {
                y = values[0] + oy;
            }
            segments.push(['L', x, y]);
            lastControl = null;
            lastQuadratic = null;
            break;
        case 'C':
        case 'S': {
            let c1x;
            let c1y;
            let rest = values;
            if (upper === 'C') {
                c1x = values[0] + ox;
                c1y = values[1] + oy;
                rest = values.slice(2);
            } else {
                c1x = lastControl ? 2 * x - lastControl[0] : x;
                c1y = lastControl ? 2 * y - lastControl[1] : y;
            }
            const c2x = rest[0] + ox;
            const c2y = rest[1] + oy;
            x = rest[2] + ox;
            y = rest[3] + oy;
            segments.push(['C', c1x, c1y, c2x, c2y, x, y]);
            lastControl = [c2x, c2y];
            lastQuadratic = null;
            break;
        }
        case 'Q':
        case 'T': {
            let cx;
            let cy;
            if (upper === 'Q') {
                cx = values[0] + ox;
                cy = values[1] + oy;
                x = values[2] + ox;
                y = values[3] + oy;
            } else {
                cx = lastQuadratic ? 2 * x - lastQuadratic[0] : x;
                cy = lastQuadratic ? 2 * y - lastQuadratic[1] : y;
                x = values[0] + ox;
                y = values[1] + oy;
            }
            segments.push(['Q', cx, cy, x, y]);
            lastQuadratic = [cx, cy];
            lastControl = null;
            break;
        }
        default: {
            const [rx, ry, rotation, large, sweep] = values;
            const ex = values[5] + ox;
            const ey = values[6] + oy;
            if (rx === 0 || ry === 0) segments.push(['L', ex, ey]);
            else segments.push(['A', Math.abs(rx), Math.abs(ry), rotation, large, sweep, ex, ey]);
            x = ex;
            y = ey;
            lastControl = null;
            lastQuadratic = null;
        }
        }
    }
    return segments;
}

function serializePath(segments, dx = 0, dy = 0) {
    let out = '';
    for (const segment of segments) {
        const [kind, ...values] = segment;
        out += kind;
        if (kind === 'A') {
            out += [values[0], values[1], values[2]].map(fmt).join(' ') +
                ` ${values[3]} ${values[4]} ` + fmt(values[5] + dx) + ' ' + fmt(values[6] + dy);
        } else {
            out += values.map((value, index) => fmt(value + (index % 2 === 0 ? dx : dy))).join(' ');
        }
    }
    return out;
}

function quadraticRoots(a, b, c) {
    if (Math.abs(a) < 1e-12) return Math.abs(b) < 1e-12 ? [] : [-c / b];
    const discriminant = b * b - 4 * a * c;
    if (discriminant < 0) return [];
    const root = Math.sqrt(discriminant);
    return [(-b + root) / (2 * a), (-b - root) / (2 * a)];
}

function arcCenter(x1, y1, segment) {
    let [, rx, ry, rotation, large, sweep, x2, y2] = segment;
    const phi = rotation * Math.PI / 180;
    const cos = Math.cos(phi);
    const sin = Math.sin(phi);
    const dx = (x1 - x2) / 2;
    const dy = (y1 - y2) / 2;
    const xp = cos * dx + sin * dy;
    const yp = -sin * dx + cos * dy;
    const lambda = (xp * xp) / (rx * rx) + (yp * yp) / (ry * ry);
    if (lambda > 1) {
        rx *= Math.sqrt(lambda);
        ry *= Math.sqrt(lambda);
    }
    const numerator = rx * rx * ry * ry - rx * rx * yp * yp - ry * ry * xp * xp;
    const denominator = rx * rx * yp * yp + ry * ry * xp * xp;
    const coefficient = (large === sweep ? -1 : 1) *
        Math.sqrt(Math.max(0, denominator === 0 ? 0 : numerator / denominator));
    const cxp = coefficient * rx * yp / ry;
    const cyp = -coefficient * ry * xp / rx;
    const cx = cos * cxp - sin * cyp + (x1 + x2) / 2;
    const cy = sin * cxp + cos * cyp + (y1 + y2) / 2;
    const angle = (ux, uy, vx, vy) => Math.atan2(ux * vy - uy * vx, ux * vx + uy * vy);
    const theta = angle(1, 0, (xp - cxp) / rx, (yp - cyp) / ry);
    let delta = angle((xp - cxp) / rx, (yp - cyp) / ry, (-xp - cxp) / rx, (-yp - cyp) / ry);
    if (!sweep && delta > 0) delta -= 2 * Math.PI;
    if (sweep && delta < 0) delta += 2 * Math.PI;
    return { cx, cy, rx, ry, cos, sin, theta, delta };
}

// The tight bounding box of the geometry, as SVG's getBBox defines it, or
// null for a path with no extent at all.
function pathBounds(segments) {
    let left = Infinity;
    let top = Infinity;
    let right = -Infinity;
    let bottom = -Infinity;
    const include = (px, py) => {
        left = Math.min(left, px);
        right = Math.max(right, px);
        top = Math.min(top, py);
        bottom = Math.max(bottom, py);
    };
    let x = 0;
    let y = 0;
    let startX = 0;
    let startY = 0;
    for (const segment of segments) {
        const kind = segment[0];
        if (kind === 'M') {
            [x, y] = [segment[1], segment[2]];
            [startX, startY] = [x, y];
            include(x, y);
        } else if (kind === 'L') {
            [x, y] = [segment[1], segment[2]];
            include(x, y);
        } else if (kind === 'Z') {
            [x, y] = [startX, startY];
        } else if (kind === 'Q') {
            const [, cx, cy, ex, ey] = segment;
            for (const [p0, p1, p2, axis] of [[x, cx, ex, 0], [y, cy, ey, 1]]) {
                const denominator = p0 - 2 * p1 + p2;
                if (Math.abs(denominator) > 1e-12) {
                    const t = (p0 - p1) / denominator;
                    if (t > 0 && t < 1) {
                        const u = 1 - t;
                        const px = u * u * x + 2 * u * t * cx + t * t * ex;
                        const py = u * u * y + 2 * u * t * cy + t * t * ey;
                        include(px, py);
                    }
                }
                void axis;
            }
            [x, y] = [ex, ey];
            include(x, y);
        } else if (kind === 'C') {
            const [, c1x, c1y, c2x, c2y, ex, ey] = segment;
            const at = (t) => {
                const u = 1 - t;
                return [
                    u * u * u * x + 3 * u * u * t * c1x + 3 * u * t * t * c2x + t * t * t * ex,
                    u * u * u * y + 3 * u * u * t * c1y + 3 * u * t * t * c2y + t * t * t * ey,
                ];
            };
            for (const [p0, p1, p2, p3] of [[x, c1x, c2x, ex], [y, c1y, c2y, ey]]) {
                const a = -p0 + 3 * p1 - 3 * p2 + p3;
                const b = 2 * (p0 - 2 * p1 + p2);
                const c = p1 - p0;
                for (const t of quadraticRoots(a, b, c)) if (t > 0 && t < 1) include(...at(t));
            }
            [x, y] = [ex, ey];
            include(x, y);
        } else {
            const arc = arcCenter(x, y, segment);
            const point = (theta) => [
                arc.cx + arc.rx * arc.cos * Math.cos(theta) - arc.ry * arc.sin * Math.sin(theta),
                arc.cy + arc.rx * arc.sin * Math.cos(theta) + arc.ry * arc.cos * Math.sin(theta),
            ];
            const candidates = [
                Math.atan2(-arc.ry * arc.sin, arc.rx * arc.cos),
                Math.atan2(arc.ry * arc.cos, arc.rx * arc.sin),
            ];
            for (const base of candidates) {
                for (const theta of [base, base + Math.PI]) {
                    const start = Math.min(arc.theta, arc.theta + arc.delta);
                    const span = Math.abs(arc.delta);
                    const offset = (((theta - start) % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI);
                    if (offset <= span) include(...point(theta));
                }
            }
            [x, y] = [segment[6], segment[7]];
            include(x, y);
        }
    }
    if (!Number.isFinite(left)) return null;
    return { x: left, y: top, width: right - left, height: bottom - top };
}

// One path's segments must fit the renderer's path budgets once serialized.
// The length the native renderer bounds a dashed stroke with: each line and
// curve control polygon, and each arc as its ellipse's circumference.
function pathLengthBound(segments) {
    let length = 0;
    let x = 0;
    let y = 0;
    let startX = 0;
    let startY = 0;
    const to = (nextX, nextY) => {
        length += Math.hypot(nextX - x, nextY - y);
        x = nextX;
        y = nextY;
    };
    for (const segment of segments) {
        const [kind, ...values] = segment;
        if (kind === 'M') {
            [x, y] = values;
            [startX, startY] = values;
        } else if (kind === 'Z') {
            to(startX, startY);
        } else if (kind === 'A') {
            if (values[0] > 0 && values[1] > 0 && (x !== values[5] || y !== values[6])) {
                const { rx, ry } = arcCenter(x, y, segment);
                length += 2 * Math.PI * Math.max(rx, ry);
                x = values[5];
                y = values[6];
            } else {
                to(values[5], values[6]);
            }
        } else {
            for (let index = 0; index + 1 < values.length; index += 2) to(values[index], values[index + 1]);
        }
    }
    return length;
}

// How many dashes a dash array draws along a path, as the native renderer
// counts them: an odd array repeats once to make its period.
function dashSegments(segments, dashes) {
    const total = dashes.reduce((sum, dash) => sum + Math.fround(dash), 0);
    const odd = dashes.length % 2 !== 0;
    return pathLengthBound(segments) * (odd ? dashes.length : dashes.length / 2) /
        (odd ? 2 * total : total);
}

function checkPathBudget(segments) {
    const values = segments.reduce((sum, segment) => sum + segment.length - 1, 0);
    if (segments.length > SVG_ASSET_LIMITS.maxPathCommands ||
        values > SVG_ASSET_LIMITS.maxPathValues ||
        utf8Length(serializePath(segments)) > SVG_ASSET_LIMITS.maxPathDataBytes) {
        refuse(`a path exceeds ${SVG_ASSET_LIMITS.maxPathCommands} commands, ` +
            `${SVG_ASSET_LIMITS.maxPathValues} values or ${SVG_ASSET_LIMITS.maxPathDataBytes} bytes`);
    }
    return segments;
}

function hasCurves(segments) {
    return segments.some((segment) => segment[0] === 'C' || segment[0] === 'Q' || segment[0] === 'A');
}

// ---------------------------------------------------------------------------
// Shapes.

// Each element's geometry is parsed once, however many uses draw it; the
// losses its parse found are reported again for each instance.
const PARSED = new WeakMap();

function shapeSegments(node, report) {
    if (!PARSED.has(node)) {
        const found = [];
        const segments = parseShapeSegments(node, (code, message) => found.push([code, message]));
        PARSED.set(node, { segments, found });
    }
    const { segments, found } = PARSED.get(node);
    for (const [code, message] of found) report(code, message);
    return segments;
}

function parseShapeSegments(node, report) {
    const a = node.attributes;
    const length = (name, fallback = 0) => {
        const value = parseLength(a[name]);
        if (value === null) {
            report('svg-length-unit-dropped', `the ${name} of <${node.name}> uses a unit other than px; 0 was used`);
            return fallback;
        }
        return value ?? fallback;
    };
    switch (node.name) {
    case 'path': {
        if (a.d === undefined || !a.d.trim()) return [];
        if (utf8Length(a.d) > SVG_ASSET_LIMITS.maxPathDataBytes) {
            refuse(`a path has more than ${SVG_ASSET_LIMITS.maxPathDataBytes} bytes of path data`);
        }
        const segments = parsePathData(a.d);
        if (segments === null) {
            report('svg-path-data-invalid', 'a path with malformed path data was dropped');
            return [];
        }
        return checkPathBudget(segments);
    }
    case 'rect': {
        const x = length('x');
        const y = length('y');
        const width = length('width');
        const height = length('height');
        if (!(width > 0) || !(height > 0)) return [];
        let rx = a.rx === undefined ? undefined : length('rx');
        let ry = a.ry === undefined ? undefined : length('ry');
        if (rx === undefined) rx = ry ?? 0;
        if (ry === undefined) ry = rx;
        rx = Math.min(Math.max(0, rx), width / 2);
        ry = Math.min(Math.max(0, ry), height / 2);
        if (rx === 0 || ry === 0) {
            return [['M', x, y], ['L', x + width, y], ['L', x + width, y + height],
                ['L', x, y + height], ['Z']];
        }
        return [['M', x + rx, y], ['L', x + width - rx, y],
            ['A', rx, ry, 0, 0, 1, x + width, y + ry], ['L', x + width, y + height - ry],
            ['A', rx, ry, 0, 0, 1, x + width - rx, y + height], ['L', x + rx, y + height],
            ['A', rx, ry, 0, 0, 1, x, y + height - ry], ['L', x, y + ry],
            ['A', rx, ry, 0, 0, 1, x + rx, y], ['Z']];
    }
    case 'circle':
    case 'ellipse': {
        const cx = length('cx');
        const cy = length('cy');
        const rx = node.name === 'circle' ? length('r') : length('rx');
        const ry = node.name === 'circle' ? rx : length('ry');
        if (!(rx > 0) || !(ry > 0)) return [];
        return [['M', cx + rx, cy], ['A', rx, ry, 0, 0, 1, cx - rx, cy],
            ['A', rx, ry, 0, 0, 1, cx + rx, cy], ['Z']];
    }
    case 'line':
        return [['M', length('x1'), length('y1')], ['L', length('x2'), length('y2')]];
    default: {
        const numbers = (a.points ?? '').trim().split(/[\s,]+/).filter(Boolean).map(Number);
        if (numbers.some((value) => !Number.isFinite(value))) {
            report('svg-points-invalid', `<${node.name}> has malformed points and was dropped`);
            return [];
        }
        if (numbers.length % 2 !== 0) {
            report('svg-points-odd', `<${node.name}> has an odd number of coordinates; the last was dropped`);
            numbers.pop();
        }
        if (numbers.length < 4) return [];
        const segments = [['M', numbers[0], numbers[1]]];
        for (let index = 2; index < numbers.length; index += 2) {
            segments.push(['L', numbers[index], numbers[index + 1]]);
        }
        if (node.name === 'polygon') segments.push(['Z']);
        return checkPathBudget(segments);
    }
    }
}

// ---------------------------------------------------------------------------
// Presentation state.

const SHAPES = new Set(['path', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon']);
const CONTAINERS = new Set(['svg', 'g', 'a', 'switch']);
// Elements that never draw where they are written; a pattern, filter or
// marker draws only through a reference, which is reported where it is made.
const DEFINITIONS = new Set(['defs', 'symbol', 'clipPath', 'mask', 'linearGradient',
    'radialGradient', 'stop', 'pattern', 'filter', 'marker']);
const SILENT = new Set(['title', 'desc', 'metadata']);
const RASTER_ELEMENTS = new Set(['text', 'image', 'pattern', 'filter', 'marker']);
const ANIMATION = new Set(['animate', 'animateTransform', 'animateMotion', 'set', 'animateColor']);
const INHERITED = ['fill', 'fill-opacity', 'fill-rule', 'stroke', 'stroke-opacity',
    'stroke-width', 'stroke-linecap', 'stroke-linejoin', 'stroke-miterlimit',
    'stroke-dasharray', 'stroke-dashoffset', 'color', 'visibility', 'clip-rule',
    'color-interpolation'];
// Every attribute and property the importer knows, and what lowering does
// with it: 'cascade' reads it from the attribute or a style, 'attribute' only
// from the attribute (a style value is a loss, and CSS geometry would need a
// rasterizer), 'raster' means only features this importer drops draw it,
// 'unsupported' is a loss, and 'quiet' never changes what is drawn. Nothing
// outside the table counts as handled.
const PROPERTIES = new Map();
const declare = (lowered, names) => {
    for (const name of names) PROPERTIES.set(name, { lowered });
};
declare('cascade', ['fill', 'stroke']);
declare('cascade', ['color', 'stop-color']);
declare('cascade', ['clip-path', 'mask', 'filter', 'marker-start', 'marker-mid',
    'marker-end']);
declare('cascade', ['transform']);
declare('cascade', ['fill-opacity', 'fill-rule', 'stroke-opacity', 'stroke-width',
    'stroke-linecap', 'stroke-linejoin', 'stroke-miterlimit', 'stroke-dasharray',
    'stroke-dashoffset', 'visibility', 'clip-rule', 'color-interpolation', 'opacity', 'display',
    'overflow', 'stop-opacity', 'offset', 'mask-type']);
declare('attribute', ['x', 'y', 'width', 'height', 'cx', 'cy', 'r', 'rx', 'ry', 'x1',
    'y1', 'x2', 'y2', 'fx', 'fy', 'fr', 'd', 'points', 'viewBox', 'preserveAspectRatio',
    'gradientUnits', 'spreadMethod', 'clipPathUnits', 'maskUnits', 'maskContentUnits']);
declare('attribute', ['gradientTransform']);
declare('raster', ['patternTransform']);
declare('raster', ['flood-color', 'lighting-color']);
declare('raster', ['font-family']);
declare('raster', ['patternUnits', 'patternContentUnits', 'filterUnits',
    'primitiveUnits', 'markerUnits', 'markerWidth', 'markerHeight', 'refX', 'refY', 'orient',
    'color-interpolation-filters', 'flood-opacity', 'xml:space',
    'font-size', 'font-weight', 'font-style', 'font-variant', 'font-stretch', 'text-anchor',
    'dominant-baseline', 'alignment-baseline', 'baseline-shift', 'letter-spacing',
    'word-spacing', 'text-decoration', 'writing-mode', 'direction', 'unicode-bidi',
    'text-rendering', 'startOffset', 'method', 'spacing', 'side', 'textLength', 'lengthAdjust',
    'dx', 'dy', 'rotate',
    'in', 'in2', 'result', 'stdDeviation', 'mode', 'operator', 'k1', 'k2', 'k3', 'k4', 'type',
    'values', 'tableValues', 'slope', 'intercept', 'amplitude', 'exponent', 'radius', 'scale',
    'xChannelSelector', 'yChannelSelector', 'baseFrequency', 'numOctaves', 'seed',
    'stitchTiles', 'order', 'kernelMatrix', 'divisor', 'bias', 'targetX', 'targetY', 'edgeMode',
    'preserveAlpha', 'surfaceScale', 'specularConstant', 'specularExponent', 'diffuseConstant',
    'kernelUnitLength', 'azimuth', 'elevation', 'z', 'pointsAtX', 'pointsAtY', 'pointsAtZ',
    'limitingConeAngle']);
declare('unsupported', ['pathLength', 'paint-order', 'vector-effect', 'shape-rendering',
    'mix-blend-mode', 'isolation']);
declare('quiet', ['id', 'style', 'class', 'role', 'lang', 'xml:lang', 'version',
    'baseProfile', 'cursor', 'pointer-events', 'tabindex', 'focusable', 'requiredFeatures',
    'requiredExtensions', 'systemLanguage', 'href', 'xlink:href']);
// Geometry CSS can set, which shapes read only from attributes.
const CSS_GEOMETRY = new Set(['x', 'y', 'width', 'height', 'cx', 'cy', 'r', 'rx', 'ry', 'd']);

function propertyOf(name) {
    if (name.startsWith('aria-') || name.startsWith('data-')) return { lowered: 'quiet' };
    return PROPERTIES.get(name);
}

// The CSS geometry a style sets on an element, which lowering does not read.
function styledGeometry(node) {
    if (node.attributes.style === undefined) return [];
    return Object.keys(styleDeclarations(node.attributes.style, `<${node.name}>`))
        .filter((name) => CSS_GEOMETRY.has(name));
}
const INITIAL = Object.freeze({
    fill: 'black', 'fill-opacity': '1', 'fill-rule': 'nonzero', stroke: 'none',
    'stroke-opacity': '1', 'stroke-width': '1', 'stroke-linecap': 'butt',
    'stroke-linejoin': 'miter', 'stroke-miterlimit': '4', 'stroke-dasharray': 'none',
    'stroke-dashoffset': '0', color: 'black', visibility: 'visible', 'clip-rule': 'nonzero',
    'color-interpolation': 'sRGB',
});

// Each element's declarations are resolved once, so an `inherit` that reads
// its parent's costs one lookup however many attributes use it.
const DECLARED = new WeakMap();

function declared(node) {
    if (!DECLARED.has(node)) DECLARED.set(node, resolveDeclared(node));
    return Object.assign(Object.create(null), DECLARED.get(node));
}

function resolveDeclared(node) {
    const values = Object.create(null);
    for (const [name, value] of Object.entries(node.attributes)) values[name] = value.trim();
    if (node.attributes.style !== undefined) {
        Object.assign(values, styleDeclarations(node.attributes.style, `<${node.name}>`));
    }
    // An inherited property's `inherit` is its default; any other takes the
    // parent's value, as CSS defines it.
    for (const [name, value] of Object.entries(values)) {
        if (value !== 'inherit') continue;
        const inherited = INHERITED.includes(name) || !node.parent ? undefined : declared(node.parent)[name];
        if (inherited === undefined) delete values[name];
        else values[name] = inherited;
    }
    return values;
}

const ENUMERATED = {
    'fill-rule': ['nonzero', 'evenodd'], 'clip-rule': ['nonzero', 'evenodd'],
    'stroke-linecap': ['butt', 'round', 'square'],
    'stroke-linejoin': ['miter', 'round', 'bevel', 'arcs', 'miter-clip'],
    visibility: ['visible', 'hidden', 'collapse'],
    'color-interpolation': ['auto', 'sRGB', 'linearRGB'],
};

function validProperty(name, value) {
    if (ENUMERATED[name]) return ENUMERATED[name].includes(value);
    if (name === 'fill' || name === 'stroke') {
        const url = leadingUrl(value);
        if (url && url.id === null) return false;
        const color = url ? url.rest : value;
        return (url !== null && color === '') || color === 'none' || color === 'currentColor' ||
            parseColor(color) !== null;
    }
    if (name === 'color') return parseColor(value) !== null;
    if (name === 'fill-opacity' || name === 'stroke-opacity') {
        const parsed = parseFraction(value, 1);
        return parsed !== null;
    }
    if (name === 'stroke-width' || name === 'stroke-dashoffset') return parseLength(value) !== null;
    if (name === 'stroke-miterlimit') {
        const parsed = parseNumber(value);
        return parsed !== null && parsed >= 1;
    }
    if (name === 'stroke-dasharray') {
        return value === 'none' || value.split(/[\s,]+/).filter(Boolean)
            .every((part) => { const length = parseLength(part); return length !== null && length >= 0; });
    }
    return true;
}

// ---------------------------------------------------------------------------
// The importer.

// SHA-256 in plain JavaScript, so the package keeps running in a browser.
const SHA256_K = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

// An authored id enters an identity as its length, "_", then the id, after a
// ":". Generated parts after a ":" start with a letter, so an identity
// splits back into its parts one way only and no two distinct paths collide.
function escapeId(id) {
    return `${id.length}_${id}`;
}

function sha256(text) {
    const bytes = new TextEncoder().encode(text);
    const length = Math.ceil((bytes.length + 9) / 64) * 64;
    const padded = new Uint8Array(length);
    padded.set(bytes);
    padded[bytes.length] = 0x80;
    const view = new DataView(padded.buffer);
    view.setUint32(length - 8, Math.floor(bytes.length / 0x20000000));
    view.setUint32(length - 4, (bytes.length * 8) >>> 0);
    const hash = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
        0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
    const words = new Uint32Array(64);
    const rotate = (value, bits) => (value >>> bits) | (value << (32 - bits));
    for (let offset = 0; offset < length; offset += 64) {
        for (let index = 0; index < 16; index += 1) words[index] = view.getUint32(offset + index * 4);
        for (let index = 16; index < 64; index += 1) {
            const a = words[index - 15];
            const b = words[index - 2];
            const s0 = rotate(a, 7) ^ rotate(a, 18) ^ (a >>> 3);
            const s1 = rotate(b, 17) ^ rotate(b, 19) ^ (b >>> 10);
            words[index] = (words[index - 16] + s0 + words[index - 7] + s1) >>> 0;
        }
        let [a, b, c, d, e, f, g, h] = hash;
        for (let index = 0; index < 64; index += 1) {
            const s1 = rotate(e, 6) ^ rotate(e, 11) ^ rotate(e, 25);
            const choice = (e & f) ^ (~e & g);
            const t1 = (h + s1 + choice + SHA256_K[index] + words[index]) >>> 0;
            const s0 = rotate(a, 2) ^ rotate(a, 13) ^ rotate(a, 22);
            const majority = (a & b) ^ (a & c) ^ (b & c);
            const t2 = (s0 + majority) >>> 0;
            [h, g, f, e, d, c, b, a] = [g, f, e, (d + t1) >>> 0, c, b, a, (t1 + t2) >>> 0];
        }
        [a, b, c, d, e, f, g, h].forEach((value, index) => { hash[index] = (hash[index] + value) >>> 0; });
    }
    return `sha256:${[...hash].map((value) => value.toString(16).padStart(8, '0')).join('')}`;
}

function utf8Length(text) {
    return new TextEncoder().encode(text).length;
}

class Importer {
    constructor(svg, options) {
        this.svg = svg;
        this.options = options;
        this.diagnostics = [];
        this.compiledOnly = [];
        this.rasterOnly = [];
        this.tokensBound = new Set();
        this.tokenMatches = new Set();
        this.nodeCount = 0;
        this.pathBytes = 0;
        this.expanded = 0;
        this.nesting = 0;
        this.root = parseXml(svg);
        this.ids = checkHostility(this.root);
        this.parents = new Map();
        this.states = new Map();
        const link = (node) => node.children.forEach((child) => {
            this.parents.set(child, node);
            link(child);
        });
        link(this.root);
        this.rootId = options.sourceId;
        this.colorTokens = new Map(Object.entries(options.colorTokens ?? {})
            .map(([color, token]) => [color.toLowerCase(), token]));
    }

    // The DesignIR identity a source id normalizes to, which a `maskRef`
    // names; it needs the document's source key.
    identityOf(sourceId) {
        if (typeof this.options.sourceKey !== 'string') {
            throw new TypeError('an SVG asset with a mask or a multi-shape clip needs the sourceKey option');
        }
        return namespacedIdentity(this.options.sourceKey, sourceId);
    }

    report(code, path, message, disposition = 'unsupported', severity = 'warning') {
        this.diagnostics.push({ code, disposition, message, path, severity });
    }

    // The drawable intermediate tree: groups and shapes with resolved paint.
    build() {
        const svg = this.root;
        const a = svg.attributes;
        const box = a.viewBox === undefined ? null
            : a.viewBox.trim().split(/[\s,]+/).map(Number);
        if (box !== null && (box.length !== 4 || box.some((value) => !Number.isFinite(value)) ||
            !(box[2] > 0) || !(box[3] > 0))) {
            refuse('the viewBox must be four numbers with a positive width and height');
        }
        const lengthOr = (name, fallback) => {
            const value = parseLength(a[name]);
            if (value === null) {
                if (!/^\s*100%\s*$/.test(a[name])) {
                    this.report('svg-length-unit-dropped', `${this.rootId}`,
                        `the root ${name} uses a unit other than px; the view box size was used`);
                }
                return fallback;
            }
            return value ?? fallback;
        };
        const width = this.options.width ?? lengthOr('width', box ? box[2] : 300);
        const height = this.options.height ?? lengthOr('height', box ? box[3] : 150);
        if (!(width > 0) || !(height > 0) || width > SVG_ASSET_LIMITS.maxNumber ||
            height > SVG_ASSET_LIMITS.maxNumber) {
            refuse('the asset needs a positive, bounded width and height');
        }
        this.width = width;
        this.height = height;
        this.viewportSize = box ? [box[2], box[3]] : [width, height];
        let viewport = [...IDENTITY];
        if (box) viewport = this.viewBoxTransform(box, width, height, a.preserveAspectRatio, this.rootId);
        const values = declared(svg);
        if (values.transform !== undefined) {
            const parsed = parseTransform(values.transform);
            if (parsed === null) this.report('svg-transform-dropped', this.rootId, 'the root has a malformed transform, which was dropped');
            else viewport = multiply(parsed, viewport);
        }
        let opacity = 1;
        if (values.opacity !== undefined) opacity = Math.min(1, Math.max(0, parseFraction(values.opacity, 1) ?? 1));
        for (const name of ['clip-path', 'mask']) {
            if (values[name] !== undefined && values[name] !== 'none') {
                this.report('svg-root-reference-dropped', this.rootId, `${name} on the root <svg> was dropped`);
            }
        }
        // A root with display none draws nothing, so its filter needs no
        // rasterizer.
        if (values.filter !== undefined && values.filter !== 'none' && values.display !== 'none') {
            this.rasterOnly.push({ path: this.rootId, reason: 'filter needs rasterization' });
        }
        this.dropUnknownAttributes(svg, this.rootId);
        // A stylesheet anywhere, definitions included, is not applied.
        const pending = [[svg, this.rootId]];
        while (pending.length > 0) {
            const [node, path] = pending.pop();
            node.children.forEach((child, index) => {
                if (child.name === 'style') {
                    this.report('svg-style-element-dropped', `${path}.${index}`,
                        'a <style> element was dropped; CSS selectors are not applied');
                } else {
                    pending.push([child, `${path}.${index}`]);
                }
            });
        }
        // A root with display none draws nothing, whatever it contains.
        const children = values.display === 'none' ? []
            : this.children(svg, this.rootId, this.inherit(INITIAL, svg), new Set());
        // ".viewport" follows the root id; an authored id always enters
        // after a ":" with its length, so id="viewport" cannot collide with it.
        return { kind: 'group', id: `${this.rootId}.viewport`, transform: viewport, opacity,
            children, name: this.title(svg) };
    }

    viewBoxTransform(box, width, height, aspect = 'xMidYMid meet', path) {
        const [vx, vy, vw, vh] = box;
        const parts = aspect.trim().split(/\s+/);
        const align = parts[0] === 'defer' ? parts[1] : parts[0];
        const slice = parts.includes('slice');
        let sx = width / vw;
        let sy = height / vh;
        if (align !== 'none') {
            if (!/^x(Min|Mid|Max)Y(Min|Mid|Max)$/.test(align ?? '')) {
                this.report('svg-aspect-ratio-dropped', path,
                    `preserveAspectRatio ${aspect} is not understood; xMidYMid meet was used`);
            }
            const scale = slice ? Math.max(sx, sy) : Math.min(sx, sy);
            sx = scale;
            sy = scale;
        }
        let tx = -vx * sx;
        let ty = -vy * sy;
        if (align !== 'none') {
            const extraX = width - vw * sx;
            const extraY = height - vh * sy;
            const fraction = (name) => (name === 'Min' ? 0 : name === 'Mid' ? 0.5 : 1);
            const match = /^x(Min|Mid|Max)Y(Min|Mid|Max)$/.exec(align ?? 'xMidYMid') ?? [null, 'Mid', 'Mid'];
            tx += extraX * fraction(match[1]);
            ty += extraY * fraction(match[2]);
        }
        return [sx, 0, 0, sy, tx, ty];
    }

    title(node) {
        const title = node.children.find((child) => child.name === 'title');
        return title && title.text.trim() ? title.text.trim().replace(/\s+/g, ' ') : undefined;
    }

    // An invalid declaration is reported and leaves the inherited value, as
    // a CSS parser ignores it.
    inherit(parent, node, path = `<${node.name}>`) {
        const values = declared(node);
        const state = { ...parent };
        for (const name of INHERITED) {
            if (values[name] === undefined) continue;
            if (validProperty(name, values[name])) {
                state[name] = values[name];
            } else {
                this.report('svg-property-invalid', path,
                    `<${node.name}> ${name} ${values[name]} is invalid and was ignored`);
            }
        }
        return state;
    }

    // Inside a use instance, a mask or a symbol, identities come from the
    // path of the referencing element (`scoped`), so two uses of one
    // definition never share an id; elsewhere an id attribute names the node.
    children(node, path, state, active, scoped = false) {
        const output = [];
        node.children.forEach((child, index) => {
            const lowered = this.element(child, `${path}.${index}`, state, active, scoped);
            if (lowered) output.push(lowered);
        });
        return output;
    }

    sourceIdFor(node, path, scoped) {
        if (scoped) return path;
        return typeof node.attributes.id === 'string' ? `${this.rootId}:${escapeId(node.attributes.id)}` : path;
    }

    // Only what the property table says lowering reads counts as handled;
    // anything else is a loss, and CSS geometry, which shapes read only from
    // attributes, is a feature only a rasterizer draws.
    dropUnknownAttributes(node, path) {
        const styled = node.attributes.style === undefined ? {}
            : styleDeclarations(node.attributes.style, `<${node.name}>`);
        for (const name of Object.keys(declared(node))) {
            const property = propertyOf(name);
            if (!property || property.lowered === 'unsupported') {
                this.report('svg-attribute-dropped', path, `<${node.name}> ${name} is not supported and was dropped`);
            } else if (property.lowered === 'attribute' && Object.hasOwn(styled, name)) {
                // The root's size comes from its attributes or the caller, so
                // a CSS size on it is an ordinary loss.
                if (CSS_GEOMETRY.has(name) && node !== this.root) {
                    this.rasterOnly.push({ path, reason: `<${node.name}> ${name} set in a style needs rasterization` });
                } else {
                    this.report('svg-attribute-dropped', path, `<${node.name}> ${name} is read only as an attribute; its style value was dropped`);
                }
            }
        }
    }

    // Every element lowered, through any reference, counts against the same
    // limits the expansion preflight applies.
    element(node, path, state, active, scoped = false) {
        if (++this.expanded > SVG_ASSET_LIMITS.maxExpandedNodes) {
            refuse(`references expand to more than ${SVG_ASSET_LIMITS.maxExpandedNodes} nodes`);
        }
        if (++this.nesting > SVG_ASSET_LIMITS.maxExpandedDepth) {
            refuse(`references nest deeper than ${SVG_ASSET_LIMITS.maxExpandedDepth}`);
        }
        try {
            return this.lowerElement(node, path, state, active, scoped);
        } finally {
            this.nesting -= 1;
        }
    }

    lowerElement(node, path, state, active, scoped) {
        if (SILENT.has(node.name) || DEFINITIONS.has(node.name)) return null;
        if (ANIMATION.has(node.name)) {
            this.report('svg-animation-dropped', path, `<${node.name}> was dropped; the static frame is drawn`);
            return null;
        }
        if (node.name === 'style') return null;
        if (RASTER_ELEMENTS.has(node.name) || node.name === 'tspan' || node.name === 'textPath') {
            this.rasterOnly.push({ path, reason: `<${node.name}> needs rasterization` });
            return { kind: 'raster-only', path, element: node.name };
        }
        if (!SHAPES.has(node.name) && !CONTAINERS.has(node.name) && node.name !== 'use') {
            this.report('svg-element-dropped', path, `<${node.name}> is not supported and was dropped`);
            return null;
        }
        const values = declared(node);
        if (values.display === 'none') return null;
        const inner = this.inherit(state, node);
        this.dropUnknownAttributes(node, path);
        for (const name of ['filter', 'marker-start', 'marker-mid', 'marker-end']) {
            if (values[name] !== undefined && values[name] !== 'none') {
                this.rasterOnly.push({ path, reason: `${name} needs rasterization` });
            }
        }
        const sourceId = this.sourceIdFor(node, path, scoped);
        let transform = [...IDENTITY];
        if (values.transform !== undefined) {
            const parsed = parseTransform(values.transform);
            if (parsed === null) {
                this.report('svg-transform-dropped', path, `<${node.name}> has a malformed transform, which was dropped`);
            } else {
                transform = parsed;
            }
        }
        let opacity = 1;
        if (values.opacity !== undefined) {
            const parsed = parseFraction(values.opacity, 1);
            if (parsed === null) this.report('svg-opacity-dropped', path, `<${node.name}> has a malformed opacity`);
            else opacity = Math.min(1, Math.max(0, parsed));
        }
        const group = { kind: 'group', id: sourceId, transform, opacity, children: [],
            name: this.title(node) };
        if (node.name === 'use') {
            const targetId = localReference(node.attributes.href ?? node.attributes['xlink:href'] ?? '');
            const target = targetId === null ? null : this.ids.get(targetId);
            if (!target) {
                this.report('svg-use-unresolved', path, `<use> names ${targetId ?? 'nothing'}, which is not in the asset`);
                return null;
            }
            const x = this.lengthOrZero(node, 'x', path);
            const y = this.lengthOrZero(node, 'y', path);
            group.transform = multiply(group.transform, [1, 0, 0, 1, x, y]);
            const nextActive = new Set(active).add(targetId);
            // A use that sets a zero or negative width or height on an svg
            // or symbol it names establishes an empty viewport, which draws
            // nothing.
            if ((target.name === 'svg' || target.name === 'symbol') &&
                ['width', 'height'].some((name) => parseLength(node.attributes[name]) <= 0)) {
                return null;
            }
            if (target.name === 'symbol') {
                const box = target.attributes.viewBox?.trim().split(/[\s,]+/).map(Number);
                const width = parseLength(node.attributes.width) ?? parseLength(target.attributes.width);
                const height = parseLength(node.attributes.height) ?? parseLength(target.attributes.height);
                if (box && box.length === 4 && box[2] > 0 && box[3] > 0) {
                    if (width > 0 && height > 0) {
                        // The viewport clip is in the use element's output
                        // units, so it applies before the viewBox transform,
                        // as a nested <svg> clips. It has a group of its own
                        // so a clip-path on the use element still applies.
                        // The symbol's own opacity, clip and mask are in its
                        // user space, inside the viewBox transform; the
                        // viewport clip is in output units, outside it.
                        const content = this.inViewport([box[2], box[3]], () => this.children(target,
                            `${sourceId}:${escapeId(targetId)}`, this.inherit(inner, target), nextActive, true));
                        const scaled = { kind: 'group', id: `${sourceId}:${escapeId(targetId)}`,
                            transform: this.viewBoxTransform(box, width, height,
                                target.attributes.preserveAspectRatio, path),
                            opacity: 1,
                            children: [this.symbolGroup(target, `${sourceId}:${escapeId(targetId)}.symbol`,
                                content, path, inner, nextActive)] };
                        group.children.push(this.viewportClip(`${sourceId}:${escapeId(targetId)}.viewport`,
                            declared(target).overflow ?? 'hidden', width, height, [scaled]));
                    } else {
                        this.report('svg-symbol-viewbox-dropped', path,
                            `<use> of a symbol with a viewBox needs a width and height; the viewBox was ignored`);
                        group.children.push(this.symbolGroup(target, `${sourceId}:${escapeId(targetId)}.symbol`,
                            this.instanceChildren(target, sourceId, targetId, inner, nextActive), path, inner, nextActive));
                    }
                } else {
                    // Without a viewBox a symbol still sets a viewport: the
                    // use's width and height, the symbol's, or the whole of
                    // the viewport it is drawn in. It clips unless its
                    // overflow is visible, and gradient percentages are of it.
                    for (const name of ['width', 'height']) {
                        const given = node.attributes[name] ?? target.attributes[name];
                        if (given !== undefined && parseLength(given) === null) {
                            this.report('svg-length-unit-dropped', path, `the ${name} of a symbol instance uses a unit other than px; the viewport size was used`);
                        }
                    }
                    const size = width > 0 && height > 0 ? [width, height] : [...this.viewportSize];
                    const content = this.inViewport(size,
                        () => this.instanceChildren(target, sourceId, targetId, inner, nextActive));
                    group.children.push(this.viewportClip(`${sourceId}:${escapeId(targetId)}.viewport`,
                        declared(target).overflow ?? 'hidden', size[0], size[1],
                        [this.symbolGroup(target, `${sourceId}:${escapeId(targetId)}.symbol`, content, path, inner, nextActive)]));
                }
            } else {
                const outer = this.useSize;
                this.useSize = target.name === 'svg' ? { target,
                    width: parseLength(node.attributes.width), height: parseLength(node.attributes.height) } : undefined;
                try {
                    const lowered = this.element(target, `${sourceId}:${escapeId(targetId)}`, inner, nextActive, true);
                    if (lowered) group.children.push(lowered);
                } finally {
                    this.useSize = outer;
                }
            }
        } else if (CONTAINERS.has(node.name)) {
            if (node.name === 'a') {
                this.report('svg-link-dropped', path, 'the link of an <a> element was dropped; its content is drawn');
            }
            if (node.name === 'switch') {
                this.report('svg-switch-approximated', path, '<switch> draws its first child; conditions are not evaluated');
                const first = node.children.find((child) => !SILENT.has(child.name));
                if (first) {
                    const lowered = this.element(first, `${sourceId}.0`, inner, active, scoped);
                    if (lowered) group.children.push(lowered);
                }
            } else if (node.name === 'svg') {
                const x = this.lengthOrZero(node, 'x', path);
                const y = this.lengthOrZero(node, 'y', path);
                // A use that names this svg sets its width and height.
                const instance = this.useSize?.target === node ? this.useSize : {};
                const width = typeof instance.width === 'number' ? instance.width : parseLength(node.attributes.width);
                const height = typeof instance.height === 'number' ? instance.height : parseLength(node.attributes.height);
                const box = node.attributes.viewBox?.trim().split(/[\s,]+/).map(Number);
                group.transform = multiply(group.transform, [1, 0, 0, 1, x, y]);
                const hasBox = box && box.length === 4 && box[2] > 0 && box[3] > 0;
                const size = hasBox ? [box[2], box[3]] : width > 0 && height > 0 ? [width, height] : null;
                const inside = this.inViewport(size,
                    () => this.children(node, scoped ? sourceId : path, inner, active, scoped));
                if (width > 0 && height > 0) {
                    const content = hasBox
                        ? [{ kind: 'group', id: `${sourceId}:viewport`,
                            transform: this.viewBoxTransform(box, width, height, node.attributes.preserveAspectRatio, path),
                            opacity: 1, children: inside }]
                        : inside;
                    group.children.push(this.viewportClip(`${sourceId}.clip`, values.overflow, width, height, content));
                } else {
                    this.report('svg-nested-viewport-dropped', path, 'a nested <svg> without a px width and height draws unclipped');
                    group.children.push(...inside);
                }
            } else {
                group.children.push(...this.children(node, scoped ? sourceId : path, inner, active, scoped));
            }
        } else {
            node.children.forEach((child, index) => {
                if (SILENT.has(child.name)) return;
                this.report(ANIMATION.has(child.name) ? 'svg-animation-dropped' : 'svg-element-dropped',
                    `${path}.${index}`, ANIMATION.has(child.name)
                        ? `<${child.name}> was dropped; the static frame is drawn`
                        : `<${child.name}> inside <${node.name}> was dropped`);
            });
            const shape = this.shape(node, sourceId, path, inner);
            if (!shape) return null;
            if (isIdentity(transform) && opacity === 1 && values['clip-path'] === undefined &&
                values.mask === undefined) {
                return shape;
            }
            shape.id = `${sourceId}:shape`;
            group.children.push(shape);
        }
        this.clipAndMask(group, values, path, inner, active);
        return group;
    }

    // User-space gradient percentages are of the nearest viewport, so a
    // nested <svg> or a symbol instance sets it while its content lowers.
    inViewport(size, lower) {
        if (size === null) return lower();
        const outer = this.viewportSize;
        this.viewportSize = size;
        try {
            return lower();
        } finally {
            this.viewportSize = outer;
        }
    }

    // A nested viewport clips to its box unless its overflow is visible or
    // auto. The clip has a group of its own, so a clip-path on the element
    // still applies.
    viewportClip(id, overflow, width, height, children) {
        const visible = overflow === 'visible' || overflow === 'auto';
        return { kind: 'group', id, transform: [...IDENTITY], opacity: 1, children,
            ...(visible ? {} : { clip: { units: 'user', shapes: [{ segments: [['M', 0, 0], ['L', width, 0], ['L', width, height], ['L', 0, height], ['Z']], rule: 'nonzero', transform: [...IDENTITY] }] } }) };
    }

    // A symbol's own opacity, clip-path, mask and filter apply to its
    // instance as one group.
    symbolGroup(target, id, children, path, inner, active) {
        const values = declared(target);
        const group = { kind: 'group', id, transform: [...IDENTITY], opacity: 1, children };
        if (values.opacity !== undefined) {
            const parsed = parseFraction(values.opacity, 1);
            if (parsed === null) this.report('svg-opacity-dropped', path, '<symbol> has a malformed opacity');
            else group.opacity = Math.min(1, Math.max(0, parsed));
        }
        if (values.filter !== undefined && values.filter !== 'none') {
            this.rasterOnly.push({ path, reason: 'a symbol filter needs rasterization' });
        }
        this.clipAndMask(group, values, path, inner, active);
        return group;
    }

    // The presentation state an element inherits along its own ancestry, as
    // a gradient, stop or clipPath child resolves currentColor,
    // color-interpolation and clip-rule. Invalid declarations are skipped,
    // as `inherit` skips them.
    stateOf(node) {
        if (this.states.has(node)) return this.states.get(node);
        const parent = this.parents.get(node);
        const state = { ...(parent ? this.stateOf(parent) : INITIAL) };
        const values = declared(node);
        for (const name of INHERITED) {
            if (values[name] !== undefined && validProperty(name, values[name])) state[name] = values[name];
        }
        this.states.set(node, state);
        return state;
    }

    lengthOrZero(node, name, path) {
        const value = parseLength(node.attributes[name]);
        if (value === null) {
            this.report('svg-length-unit-dropped', path, `the ${name} of <${node.name}> uses a unit other than px; 0 was used`);
            return 0;
        }
        return value ?? 0;
    }

    instanceChildren(target, sourceId, targetId, inner, active) {
        return this.children(target, `${sourceId}:${escapeId(targetId)}`, this.inherit(inner, target, `${sourceId}`), active, true);
    }

    // A fallback is a colour or none, never another reference, so the
    // fallback a paint names resolves without recursing again.
    paint(value, opacity, path, property, state, fallback = false) {
        if (value === undefined || value === 'none') return { kind: 'none' };
        const leading = leadingUrl(value);
        const url = leading && leading.id !== null ? [value, '', leading.id, leading.rest] : null;
        if (url && fallback) {
            this.report('svg-paint-dropped', path, `${property} has a reference as a fallback; nothing was painted`);
            return { kind: 'none' };
        }
        if (url) {
            const definition = this.ids.get(url[2]);
            if (definition && (definition.name === 'linearGradient' || definition.name === 'radialGradient')) {
                const gradient = this.gradient(definition, path, property, state);
                if (!gradient) {
                    if (url[3]) return this.paint(url[3].trim(), opacity, path, property, state, true);
                    return { kind: 'none' };
                }
                if (gradient.stops.length === 1) {
                    const [stop] = gradient.stops;
                    if (stop.color[3] * stop.opacity * opacity <= 0) return { kind: 'none' };
                    return { kind: 'color', color: stop.color, opacity: opacity * stop.opacity };
                }
                return { kind: 'gradient', gradient, opacity };
            }
            if (definition && definition.name === 'pattern') {
                this.rasterOnly.push({ path, reason: `the ${property} pattern needs rasterization` });
                return { kind: 'none' };
            }
            if (url[3]) return this.paint(url[3].trim(), opacity, path, property, state, true);
            this.report('svg-paint-unresolved', path, `${property} names ${url[2]}, which is not a gradient in the asset; nothing was painted`);
            return { kind: 'none' };
        }
        const source = value === 'currentColor' ? state.color : value;
        const color = parseColor(source);
        if (!color) {
            this.report('svg-paint-dropped', path, `${property} ${value} is not a colour this importer reads; nothing was painted`);
            return { kind: 'none' };
        }
        const hex = hexString(color);
        const effective = color[3] * opacity;
        if (effective <= 0) return { kind: 'none' };
        const token = effective === 1 ? this.colorTokens.get(hex) : undefined;
        if (token) this.tokenMatches.add(token);
        return { kind: 'color', color, opacity, token };
    }

    gradient(definition, path, property, state) {
        // Attributes and stops inherit along the href chain, nearest first.
        const chain = [];
        let current = definition;
        const seen = new Set();
        while (current && !seen.has(current)) {
            seen.add(current);
            chain.push(current);
            const next = localReference(current.attributes.href ?? current.attributes['xlink:href'] ?? '');
            current = next === null ? null : this.ids.get(next);
            if (current && current.name !== 'linearGradient' && current.name !== 'radialGradient') current = null;
        }
        const attribute = (name) => {
            for (const item of chain) {
                if (item.attributes[name] !== undefined) return item.attributes[name];
            }
            return undefined;
        };
        const stopsOwner = chain.find((item) => item.children.some((child) => child.name === 'stop'));
        const stops = [];
        let previous = 0;
        for (const stop of stopsOwner?.children.filter((child) => child.name === 'stop') ?? []) {
            const values = declared(stop);
            let offset = parseFraction(values.offset, 0);
            if (offset === null) {
                this.report('svg-stop-offset-invalid', path, `a gradient stop offset ${values.offset} is invalid; 0 was used`);
                offset = 0;
            }
            offset = Math.min(1, Math.max(previous, offset));
            previous = offset;
            const raw = values['stop-color'] === 'currentColor' ? this.stateOf(stop).color : (values['stop-color'] ?? 'black');
            const color = parseColor(raw);
            if (!color) {
                this.report('svg-stop-color-dropped', path, `a gradient stop colour ${raw} is not read; black was used`);
            }
            const opacityValue = parseFraction(values['stop-opacity'], 1);
            if (opacityValue === null) {
                this.report('svg-stop-opacity-invalid', path, `a gradient stop opacity ${values['stop-opacity']} is invalid; 1 was used`);
            }
            stops.push({ offset, color: color ?? [0, 0, 0, 1],
                opacity: Math.min(1, Math.max(0, opacityValue ?? 1)) });
        }
        if (stops.length === 0) return null;
        if (stops.length > SVG_ASSET_LIMITS.maxGradientStops) {
            this.report('svg-gradient-stops-dropped', path, `${property} gradient has ${stops.length} stops; the stops after the 64th were dropped`);
            stops.length = 64;
        }
        for (const stop of stops) {
            const token = stop.opacity * stop.color[3] === 1 ? this.colorTokens.get(hexString(stop.color)) : undefined;
            if (token) this.tokenMatches.add(token);
        }
        const kind = definition.name === 'linearGradient' ? 'linear' : 'radial';
        const units = attribute('gradientUnits') === 'userSpaceOnUse' ? 'user' : 'box';
        let transform = [...IDENTITY];
        const transformText = attribute('gradientTransform');
        if (transformText !== undefined) {
            const parsed = parseTransform(transformText);
            if (parsed === null) {
                this.report('svg-gradient-transform-dropped', path, `${property} gradient has a malformed gradientTransform, which was dropped`);
            } else {
                transform = parsed;
            }
        }
        const spread = attribute('spreadMethod') ?? 'pad';
        const geometry = {};
        // In user space a percentage, like each default, is of the viewport:
        // its width, height, or normalized diagonal.
        const [viewWidth, viewHeight] = this.viewportSize;
        const extent = { x: viewWidth, y: viewHeight,
            d: Math.sqrt((viewWidth * viewWidth + viewHeight * viewHeight) / 2) };
        const coordinate = (name, fallback, axis) => {
            const raw = attribute(name) ?? fallback;
            const trimmed = raw.trim();
            const percent = trimmed.endsWith('%') ? exactNumber(trimmed, '%') : null;
            if (percent !== null) return percent / 100 * (units === 'user' ? extent[axis] : 1);
            const value = units === 'user' ? parseLength(raw) : parseNumber(raw);
            if (value === null || value === undefined) {
                this.report('svg-gradient-coordinate-dropped', path, `${property} gradient ${name} is malformed; its default was used`);
                return coordinate(name === 'fx' || name === 'fy' ? name.replace('f', 'c') : '', fallback, axis);
            }
            return value;
        };
        if (kind === 'linear') {
            geometry.x1 = coordinate('x1', '0%', 'x');
            geometry.y1 = coordinate('y1', '0%', 'y');
            geometry.x2 = coordinate('x2', '100%', 'x');
            geometry.y2 = coordinate('y2', '0%', 'y');
        } else {
            geometry.cx = coordinate('cx', '50%', 'x');
            geometry.cy = coordinate('cy', '50%', 'y');
            geometry.r = coordinate('r', '50%', 'd');
            geometry.fx = attribute('fx') === undefined ? geometry.cx : coordinate('fx', '50%', 'x');
            geometry.fy = attribute('fy') === undefined ? geometry.cy : coordinate('fy', '50%', 'y');
            geometry.fr = coordinate('fr', '0%', 'd');
        }
        if (!['pad', 'repeat', 'reflect'].includes(spread)) {
            this.report('svg-gradient-spread-dropped', path, `${property} gradient spreadMethod ${spread} is unknown; pad was used`);
        }
        const interpolation = this.stateOf(definition)['color-interpolation'] === 'linearRGB' ? 'linear-srgb' : 'srgb';
        return { kind, units, transform, spread: ['pad', 'repeat', 'reflect'].includes(spread) ? spread : 'pad',
            geometry, stops, interpolation, id: definition.attributes.id };
    }

    shape(node, sourceId, path, state) {
        const segments = shapeSegments(node, (code, message) => this.report(code, path, message));
        if (segments.length === 0) return null;
        if (state.visibility === 'hidden' || state.visibility === 'collapse') return null;
        const fillOpacity = Math.min(1, Math.max(0, parseFraction(state['fill-opacity'], 1) ?? 1));
        const strokeOpacity = Math.min(1, Math.max(0, parseFraction(state['stroke-opacity'], 1) ?? 1));
        const fill = node.name === 'line' ? { kind: 'none' }
            : this.paint(state.fill, fillOpacity, path, 'fill', state);
        const stroke = this.paint(state.stroke, strokeOpacity, path, 'stroke', state);
        const width = parseLength(state['stroke-width']);
        const strokeWidth = width === null ? 1 : Math.max(0, width);
        if (width === null) this.report('svg-length-unit-dropped', path, 'stroke-width uses a unit other than px; 1 was used');
        let dashes;
        if (state['stroke-dasharray'] && state['stroke-dasharray'] !== 'none') {
            const parsed = state['stroke-dasharray'].split(/[\s,]+/).filter(Boolean).map((part) => parseLength(part));
            if (parsed.some((part) => part === null || part === undefined || part < 0)) {
                this.report('svg-dash-dropped', path, 'stroke-dasharray is malformed and was dropped');
            } else if (parsed.length > SVG_ASSET_LIMITS.maxDashes) {
                this.rasterOnly.push({ path, reason: `a stroke-dasharray of ${parsed.length} entries, beyond ${SVG_ASSET_LIMITS.maxDashes}, needs rasterization` });
            } else if (parsed.reduce((sum, part) => sum + part, 0) > 0) {
                const count = dashSegments(segments, parsed);
                if (count > SVG_ASSET_LIMITS.maxDashSegments) {
                    this.rasterOnly.push({ path, reason: `a stroke-dasharray that draws about ${Math.round(count)} dashes, beyond ${SVG_ASSET_LIMITS.maxDashSegments}, needs rasterization` });
                } else {
                    dashes = parsed;
                }
            }
        }
        const join = ['miter', 'round', 'bevel'].includes(state['stroke-linejoin']) ? state['stroke-linejoin'] : 'miter';
        if (!['miter', 'round', 'bevel'].includes(state['stroke-linejoin'])) {
            this.report('svg-linejoin-approximated', path, `stroke-linejoin ${state['stroke-linejoin']} is drawn as miter`);
        }
        const cap = ['butt', 'round', 'square'].includes(state['stroke-linecap']) ? state['stroke-linecap'] : 'butt';
        const miter = Math.max(1, parseNumber(state['stroke-miterlimit']) ?? 4);
        const bounds = pathBounds(segments);
        const shape = { kind: 'shape', id: sourceId, segments, bounds,
            curved: hasCurves(segments), fill, stroke: strokeWidth > 0 ? stroke : { kind: 'none' },
            fillRule: state['fill-rule'] === 'evenodd' ? 'evenodd' : 'nonzero', strokeWidth, join,
            cap, miter, dashes, dashOffset: parseLength(state['stroke-dashoffset']) ?? 0,
            name: this.title(node) };
        if (shape.fill.kind === 'none' && shape.stroke.kind === 'none') return null;
        return shape;
    }

    clipAndMask(group, values, path, state, active) {
        const clip = values['clip-path'];
        if (clip !== undefined && clip !== 'none') {
            const url = leadingUrl(clip);
            const id = url && url.rest === '' ? url.id : null;
            const definition = id === null ? null : this.ids.get(id);
            if (!definition || definition.name !== 'clipPath') {
                this.report('svg-clip-unresolved', path, `clip-path ${clip} names no clipPath in the asset; the content is hidden`);
                group.hidden = true;
            } else {
                const units = definition.attributes.clipPathUnits === 'objectBoundingBox' ? 'box' : 'user';
                let clipTransform = [...IDENTITY];
                const definitionTransform = declared(definition).transform;
                if (definitionTransform) {
                    const parsed = parseTransform(definitionTransform);
                    if (parsed === null) this.report('svg-transform-dropped', path, 'a clipPath has a malformed transform, which was dropped');
                    else clipTransform = parsed;
                }
                const shapes = [];
                for (const child of definition.children) {
                    if (SILENT.has(child.name)) continue;
                    let shapeNode = child;
                    let childTransform = [...IDENTITY];
                    if (child.name === 'use') {
                        const target = this.ids.get(localReference(child.attributes.href ?? child.attributes['xlink:href'] ?? '') ?? '');
                        if (!target || !SHAPES.has(target.name)) {
                            this.report('svg-clip-child-dropped', path, 'a clipPath <use> that does not name a shape was dropped');
                            continue;
                        }
                        // SVG applies the use element's transform, then its
                        // x and y, then the referenced shape's transform.
                        const useValues = declared(child);
                        if (useValues.display === 'none') continue;
                        if (useValues.transform !== undefined) {
                            const parsed = parseTransform(useValues.transform);
                            if (parsed === null) this.report('svg-transform-dropped', path, 'a clipPath <use> has a malformed transform, which was dropped');
                            else childTransform = parsed;
                        }
                        childTransform = multiply(childTransform,
                            [1, 0, 0, 1, this.lengthOrZero(child, 'x', path), this.lengthOrZero(child, 'y', path)]);
                        shapeNode = target;
                    }
                    if (!SHAPES.has(shapeNode.name)) {
                        if (RASTER_ELEMENTS.has(shapeNode.name)) {
                            this.rasterOnly.push({ path, reason: `a clipPath <${shapeNode.name}> needs rasterization` });
                        } else {
                            this.report('svg-clip-child-dropped', path, `a clipPath <${shapeNode.name}> was dropped`);
                        }
                        continue;
                    }
                    const childValues = declared(shapeNode);
                    if (childValues.display === 'none') continue;
                    for (const name of styledGeometry(shapeNode)) {
                        this.rasterOnly.push({ path, reason: `a clipPath <${shapeNode.name}> ${name} set in a style needs rasterization` });
                    }
                    for (const holder of new Set([child, shapeNode])) {
                        const nested = declared(holder)['clip-path'];
                        if (nested !== undefined && nested !== 'none') {
                            this.rasterOnly.push({ path, reason: 'a clip-path inside a clipPath needs rasterization' });
                        }
                    }
                    // A hidden shape contributes no clip area; visibility
                    // inherits along the clipPath, through a use to its target.
                    const visibility = childValues.visibility !== undefined &&
                        validProperty('visibility', childValues.visibility)
                        ? childValues.visibility : this.stateOf(child).visibility;
                    if (visibility === 'hidden' || visibility === 'collapse') continue;
                    if (childValues.transform) {
                        const parsed = parseTransform(childValues.transform);
                        if (parsed === null) this.report('svg-transform-dropped', path, 'a clipPath child has a malformed transform, which was dropped');
                        else childTransform = multiply(childTransform, parsed);
                    }
                    const segments = shapeSegments(shapeNode, (code, message) => this.report(code, path, message));
                    if (segments.length === 0) continue;
                    // clip-rule inherits along the clipPath's own ancestry:
                    // the shape, the use that names it, then the clipPath.
                    const ownRule = validProperty('clip-rule', childValues['clip-rule'] ?? '') ? childValues['clip-rule'] : undefined;
                    const rule = ownRule ?? this.stateOf(child)['clip-rule'];
                    shapes.push({ segments, rule: rule === 'evenodd' ? 'evenodd' : 'nonzero',
                        transform: multiply(clipTransform, childTransform) });
                }
                group.clip = { shapes, units };
                const nested = declared(definition)['clip-path'];
                if (nested !== undefined && nested !== 'none') {
                    this.rasterOnly.push({ path, reason: 'a clip-path on a clipPath needs rasterization' });
                }
            }
        }
        const mask = values.mask;
        if (mask !== undefined && mask !== 'none') {
            const url = leadingUrl(mask);
            const id = url && url.rest === '' ? url.id : null;
            const definition = id === null ? null : this.ids.get(id);
            if (!definition || definition.name !== 'mask') {
                this.report('svg-mask-unresolved', path, `mask ${mask} names no mask in the asset; the content is hidden`);
                group.hidden = true;
                return;
            }
            const maskValues = declared(definition);
            const mode = (maskValues['mask-type'] ?? 'luminance') === 'alpha' ? 'alpha' : 'luminance';
            if (definition.attributes.maskContentUnits === 'objectBoundingBox') {
                this.report('svg-mask-units-dropped', path, 'maskContentUnits objectBoundingBox is drawn in user space');
            }
            const region = ['x', 'y', 'width', 'height'].some((name) => definition.attributes[name] !== undefined) ||
                definition.attributes.maskUnits === 'userSpaceOnUse';
            if (region) {
                this.report('svg-mask-region-dropped', path, 'the explicit mask region was not applied');
            }
            this.report('svg-mask-region-unclipped', path,
                'the mask region is not applied: content farther than 10% beyond the masked bounding box is masked but not clipped');
            group.mask = { mode, id: `${group.id}:mask`,
                children: this.children(definition, `${group.id}:mask`,
                    this.inherit(this.parents.has(definition) ? this.stateOf(this.parents.get(definition)) : INITIAL, definition),
                    new Set(active).add(id), true) };
        }
    }
}

// ---------------------------------------------------------------------------
// Subtree lowering. Returns null and records why when a feature only the
// compiled form carries is met.

function subtreeGradient(gradient, why) {
    if (gradient.units !== 'box') return why('userSpaceOnUse gradient units');
    if (!isIdentity(gradient.transform)) return why('a gradientTransform');
    if (gradient.interpolation !== 'srgb') return why('linearRGB colour interpolation');
    const offsets = gradient.stops.map((stop) => stop.offset);
    for (let index = 1; index < offsets.length; index += 1) {
        if (offsets[index] <= offsets[index - 1]) return why('coincident gradient stops');
    }
    if (gradient.stops.length < 2 || gradient.stops.length > SVG_ASSET_LIMITS.maxGradientStops) {
        return why(`a gradient with fewer than two or more than ${SVG_ASSET_LIMITS.maxGradientStops} stops`);
    }
    const g = gradient.geometry;
    if (gradient.kind === 'linear') {
        if (gradient.spread !== 'pad') return why('a repeating or reflecting linear gradient');
        const horizontal = g.y1 === g.y2 && g.x1 !== g.x2;
        const vertical = g.x1 === g.x2 && g.y1 !== g.y2;
        if (!horizontal && !vertical) return why('a diagonal linear gradient');
        const [from, to] = horizontal ? [g.x1, g.x2] : [g.y1, g.y2];
        if (Math.min(from, to) < 0 || Math.max(from, to) > 1) return why('a linear gradient whose ends leave the box');
        const forward = to > from;
        const angle = horizontal ? (forward ? 90 : 270) : (forward ? 180 : 0);
        const start = forward ? from : 1 - from;
        const span = Math.abs(to - from);
        return { kind: 'linear', angle, map: (offset) => start + offset * span };
    }
    if (g.fx !== g.cx || g.fy !== g.cy || g.fr !== 0) return why('a radial gradient with a focal point');
    if (!(Math.fround(Number(fmt(g.r))) > 0)) return why('a radial gradient with no radius');
    return { kind: 'radial', centerX: g.cx, centerY: g.cy, radiusX: g.r, radiusY: g.r,
        spread: gradient.spread, map: (offset) => offset };
}

function stopColor(stop, opacity, tokens, bound) {
    const hex = hexString(stop.color, stop.opacity * opacity);
    const token = stop.opacity * opacity * stop.color[3] === 1 ? tokens.get(hex) : undefined;
    if (token) {
        bound.add(token);
        return `{${token}}`;
    }
    return hex;
}

class SubtreeLowering {
    constructor(importer) {
        this.importer = importer;
        this.blocked = [];
        this.nodes = 0;
        this.pathBytes = 0;
    }

    why(reason) {
        this.blocked.push(reason);
        return null;
    }

    paint(paint, bounds, tokens) {
        if (paint.kind === 'none') return 'none';
        if (paint.kind === 'color') {
            if (paint.token) {
                this.importer.tokensBound.add(paint.token);
                return `{${paint.token}}`;
            }
            return hexString(paint.color, paint.opacity);
        }
        // Only an objectBoundingBox gradient needs an extent; a user-space one
        // on a horizontal or vertical line still paints, so it goes on to
        // the compiled form, which carries user space.
        if (paint.gradient.units === 'box' &&
            (!bounds || !(bounds.width > 0) || !(bounds.height > 0))) {
            return 'none';
        }
        const resolved = subtreeGradient(paint.gradient, (reason) => this.why(reason));
        if (!resolved) return 'none';
        const stops = paint.gradient.stops.map((stop) => ({
            position: Number(fmt(resolved.map(stop.offset))),
            color: stopColor(stop, paint.opacity, tokens, this.importer.tokensBound),
        }));
        for (let index = 1; index < stops.length; index += 1) {
            if (Math.fround(stops[index].position) <= Math.fround(stops[index - 1].position)) {
                return this.why('gradient stops that meet after remapping or float32 rounding') ?? 'none';
            }
        }
        if (resolved.kind === 'linear') return { kind: 'linear', angle: resolved.angle, stops };
        return { kind: 'radial', spread: resolved.spread, centerX: resolved.centerX,
            centerY: resolved.centerY, radiusX: resolved.radiusX, radiusY: resolved.radiusY, stops };
    }

    shape(shape, tokens) {
        this.nodes += 1;
        const bounds = shape.bounds ?? { x: 0, y: 0, width: 0, height: 0 };
        const needsBox = shape.fill.kind === 'gradient' || shape.stroke.kind === 'gradient';
        const origin = needsBox ? bounds : { x: 0, y: 0, width: 0, height: 0 };
        const d = serializePath(shape.segments, -origin.x, -origin.y);
        this.pathBytes += utf8Length(d);
        const path = { d };
        if (shape.fillRule === 'evenodd') path.fillRule = 'evenodd';
        path.fill = this.paint(shape.fill, bounds, tokens);
        path.stroke = this.paint(shape.stroke, bounds, tokens);
        if (shape.stroke.kind !== 'none') {
            path.strokeWidth = shape.strokeWidth;
            if (shape.join !== 'miter') path.strokeLineJoin = shape.join;
            if (shape.cap !== 'butt') path.strokeLineCap = shape.cap;
            if (shape.miter !== 4) path.strokeMiterLimit = shape.miter;
            if (shape.dashes) {
                path.strokeDasharray = shape.dashes;
                if (shape.dashOffset) path.strokeDashoffset = shape.dashOffset;
            }
        }
        const node = {
            kind: 'path', sourceId: shape.id,
            properties: {
                layout: { x: Number(fmt(origin.x)), y: Number(fmt(origin.y)),
                    width: Number(fmt(origin.width)), height: Number(fmt(origin.height)) },
                path,
            },
            children: [],
        };
        if (shape.name) node.name = shape.name;
        return node;
    }

    clipShapes(clip) {
        if (clip.units === 'box') return this.why('a clipPath in objectBoundingBox units');
        return clip.shapes;
    }

    group(group, tokens) {
        this.nodes += 1;
        const paint = {};
        if (!isIdentity(group.transform)) paint.transform = `matrix(${cleanMatrix(group.transform).join(', ')})`;
        let opacity = group.hidden ? 0 : group.opacity;
        const node = {
            kind: 'view', sourceId: group.id,
            properties: { layout: { x: 0, y: 0, width: 0, height: 0 }, paint },
            children: [],
        };
        if (group.name) node.name = group.name;
        const content = [];
        for (const child of group.children) {
            const lowered = this.lower(child, tokens);
            if (lowered) content.push(lowered);
        }
        let masks = [];
        if (group.clip) {
            const shapes = this.clipShapes(group.clip);
            if (shapes && shapes.length === 0) {
                opacity = 0;
            } else if (shapes && shapes.length === 1 && isIdentity(shapes[0].transform) &&
                shapes[0].rule === 'nonzero') {
                paint.clipPath = serializePath(shapes[0].segments);
                this.pathBytes += utf8Length(paint.clipPath);
            } else if (shapes) {
                // A union of clip shapes is coverage: an alpha mask of the
                // shapes filled opaque.
                this.nodes += 1;
                const mask = { kind: 'view', sourceId: `${group.id}:clip`,
                    properties: { layout: { x: 0, y: 0, width: 0, height: 0 } },
                    compositing: { mask: 'alpha' }, children: [] };
                shapes.forEach((shape, index) => {
                    this.nodes += 2;
                    const d = serializePath(shape.segments);
                    this.pathBytes += utf8Length(d);
                    const holder = { kind: 'view', sourceId: `${group.id}:clip.${index}`,
                        properties: { layout: { x: 0, y: 0, width: 0, height: 0 }, paint: {} }, children: [] };
                    if (!isIdentity(shape.transform)) holder.properties.paint.transform = `matrix(${cleanMatrix(shape.transform).join(', ')})`;
                    holder.children.push({ kind: 'path', sourceId: `${group.id}:clip.${index}:shape`,
                        properties: { layout: { x: 0, y: 0, width: 0, height: 0 },
                            path: { d, fill: '#ffffff', stroke: 'none', ...(shape.rule === 'evenodd' ? { fillRule: 'evenodd' } : {}) } },
                        children: [] });
                    mask.children.push(holder);
                });
                masks.push(mask);
            }
        }
        if (group.mask) {
            if (group.mask.region) this.why('a mask with an explicit region');
            this.nodes += 1;
            const mask = { kind: 'view', sourceId: group.mask.id,
                properties: { layout: { x: 0, y: 0, width: 0, height: 0 } },
                compositing: { mask: group.mask.mode }, children: [] };
            for (const child of group.mask.children) {
                const lowered = this.lower(child, tokens);
                if (lowered) mask.children.push(lowered);
            }
            masks.push(mask);
        }
        // A mask applies to the siblings that name it, one mask to a parent,
        // so each mask wraps what it masks in a holder beside it: the clip's
        // coverage innermost, then the SVG mask's.
        let level = content;
        masks.forEach((mask, index) => {
            this.nodes += 1;
            const holder = { kind: 'view', sourceId: `${group.id}:${index === 0 ? 'content' : 'masked'}`,
                properties: { layout: { x: 0, y: 0, width: 0, height: 0 } },
                compositing: { maskRef: this.importer.identityOf(mask.sourceId) },
                children: level };
            level = [holder, mask];
        });
        node.children.push(...level);
        if (opacity !== 1) node.compositing = { opacity: Number(fmt(opacity)) };
        return node;
    }

    lower(item, tokens) {
        if (item.kind === 'raster-only') return null;
        return item.kind === 'shape' ? this.shape(item, tokens) : this.group(item, tokens);
    }
}

// ---------------------------------------------------------------------------
// Compiled lowering: one display list.

function compiledGradient(gradient, bounds, opacity) {
    const stops = gradient.stops.map((stop) => ({
        position: Number(fmt(stop.offset)),
        color: hexString(stop.color, stop.opacity * opacity),
    }));
    let space = gradient.units === 'box'
        ? [bounds.width, 0, 0, bounds.height, bounds.x, bounds.y]
        : [...IDENTITY];
    space = multiply(space, gradient.transform);
    const g = gradient.geometry;
    let shape;
    if (gradient.kind === 'linear') {
        const dx = g.x2 - g.x1;
        const dy = g.y2 - g.y1;
        if (dx === 0 && dy === 0) return { solid: stops.at(-1).color };
        // The unit box's horizontal ramp, carried onto the gradient vector,
        // with the perpendicular kept perpendicular in gradient space.
        space = multiply(space, [dx, dy, -dy, dx, g.x1 + dy * 0.5, g.y1 - dx * 0.5]);
        shape = { kind: 'linear', angle: 90 };
    } else {
        if (!(g.r > 0) || (g.fr >= g.r && g.fx === g.cx && g.fy === g.cy)) {
            return { solid: stops.at(-1).color };
        }
        shape = { kind: 'two-circle-radial', centerX: Number(fmt(g.cx)), centerY: Number(fmt(g.cy)),
            focalX: Number(fmt(g.fx)), focalY: Number(fmt(g.fy)), startRadius: Number(fmt(Math.max(0, g.fr))),
            endRadius: Number(fmt(g.r)) };
        // The renderer refuses identical circles, judged on the written
        // values as float32, so rounding that makes them identical paints
        // the last stop, as SVG does for a focal circle that fills the end.
        const f = Math.fround;
        if (f(shape.startRadius) === f(shape.endRadius) && f(shape.focalX) === f(shape.centerX) &&
            f(shape.focalY) === f(shape.centerY)) {
            return { solid: stops.at(-1).color, degenerate: true };
        }
    }
    // The renderer checks invertibility in float32; so does the importer.
    const f = space.map((value) => Math.fround(Number(fmt(value))));
    const determinant = Math.fround(Math.fround(f[0] * f[3]) - Math.fround(f[1] * f[2]));
    if (!Number.isFinite(determinant) || determinant === 0) return { solid: stops.at(-1).color };
    return {
        gradient: { ...shape, spread: gradient.spread,
            ...(gradient.interpolation !== 'srgb' ? { interpolation: gradient.interpolation } : {}),
            stops },
        transform: cleanMatrix(space),
    };
}

function compiledPaint(paint, bounds, degenerate = () => {}) {
    if (paint.kind === 'none') return 'none';
    if (paint.kind === 'color') return hexString(paint.color, paint.opacity);
    if (paint.gradient.units === 'box' && (!bounds || !(bounds.width > 0) || !(bounds.height > 0))) {
        return 'none';
    }
    const resolved = compiledGradient(paint.gradient, bounds, paint.opacity);
    if (resolved.degenerate) degenerate();
    if (resolved.solid) return resolved.solid;
    return { gradient: resolved.gradient, transform: resolved.transform };
}

class CompiledLowering {
    constructor(importer) {
        this.importer = importer;
        this.ops = [];
    }

    lower(item) {
        if (item.kind === 'raster-only') return;
        if (item.kind === 'shape') {
            const degenerate = () => this.importer.report('svg-gradient-degenerate', item.id,
                'a two-circle radial gradient whose circles become identical once written was drawn as its last stop');
            const op = { op: 'path', d: serializePath(item.segments),
                fill: compiledPaint(item.fill, item.bounds, degenerate),
                stroke: compiledPaint(item.stroke, item.bounds, degenerate) };
            if (item.fillRule === 'evenodd') op.fillRule = 'evenodd';
            if (op.stroke !== 'none') {
                op.strokeWidth = item.strokeWidth;
                if (item.join !== 'miter') op.strokeLineJoin = item.join;
                if (item.cap !== 'butt') op.strokeLineCap = item.cap;
                if (item.miter !== 4) op.strokeMiterLimit = item.miter;
                if (item.dashes) {
                    op.strokeDasharray = item.dashes;
                    if (item.dashOffset) op.strokeDashoffset = item.dashOffset;
                }
            }
            if (op.fill === 'none' && op.stroke === 'none') return;
            this.ops.push(op);
            return;
        }
        const op = { op: 'group' };
        if (!isIdentity(item.transform)) op.transform = cleanMatrix(item.transform);
        if (item.opacity !== 1 || item.hidden) op.opacity = item.hidden ? 0 : Number(fmt(item.opacity));
        let wrappers = 0;
        const after = [];
        if (item.clip) {
            const shapes = item.clip.shapes;
            if (item.clip.units === 'box' || shapes.length > 1) {
                this.importer.compiledLimits.push(`the clip of ${item.id}, which has ${item.clip.units === 'box' ? 'objectBoundingBox units' : `${shapes.length} shapes`}`);
            }
            if (shapes.length === 0) {
                op.opacity = 0;
            } else if (isIdentity(shapes[0].transform)) {
                op.clip = { d: serializePath(shapes[0].segments), rule: shapes[0].rule };
            } else {
                // A transformed clip shape: a group whose transform carries the
                // clip, then its inverse for the content, which is exact.
                const t = shapes[0].transform;
                const determinant = t[0] * t[3] - t[1] * t[2];
                if (determinant === 0) {
                    op.opacity = 0;
                } else {
                    const inverse = [t[3] / determinant, -t[1] / determinant, -t[2] / determinant,
                        t[0] / determinant, (t[2] * t[5] - t[3] * t[4]) / determinant,
                        (t[1] * t[4] - t[0] * t[5]) / determinant];
                    after.push({ op: 'group', transform: cleanMatrix(t),
                        clip: { d: serializePath(shapes[0].segments), rule: shapes[0].rule } });
                    after.push({ op: 'group', transform: inverse.map((value) => Number(value.toPrecision(12))) });
                    wrappers = 2;
                }
            }
        }
        if (item.mask) this.importer.compiledLimits.push(`the mask of ${item.id}`);
        this.ops.push(op, ...after);
        for (const child of item.children) this.lower(child);
        for (let index = 0; index <= wrappers; index += 1) this.ops.push({ op: 'end' });
    }
}

function countTree(item) {
    if (item.kind === 'raster-only') return { nodes: 0, bytes: 0 };
    if (item.kind === 'shape') {
        return { nodes: 1, bytes: utf8Length(serializePath(item.segments)) };
    }
    let nodes = 1;
    let bytes = 0;
    for (const child of [...item.children, ...(item.mask?.children ?? [])]) {
        const count = countTree(child);
        nodes += count.nodes;
        bytes += count.bytes;
    }
    return { nodes, bytes };
}

// The box the drawable tree can paint in the asset's viewport coordinates,
// strokes included; null when it paints nothing. Clips only shrink it, so it
// ignores them.
function paintBounds(item, matrix = IDENTITY) {
    if (item.kind === 'raster-only') return null;
    if (item.kind === 'shape') {
        if (!item.bounds) return null;
        const stroked = item.stroke.kind !== 'none';
        const factor = Math.max(1, item.join === 'miter' ? item.miter : 1,
            item.cap === 'butt' ? 1 : Math.SQRT2);
        const outset = stroked ? item.strokeWidth * factor / 2 : 0;
        const { x, y, width, height } = item.bounds;
        const corners = [[x - outset, y - outset], [x + width + outset, y - outset],
            [x - outset, y + height + outset], [x + width + outset, y + height + outset]]
            .map(([px, py]) => apply(matrix, px, py));
        return {
            left: Math.min(...corners.map((corner) => corner[0])),
            top: Math.min(...corners.map((corner) => corner[1])),
            right: Math.max(...corners.map((corner) => corner[0])),
            bottom: Math.max(...corners.map((corner) => corner[1])),
        };
    }
    const inner = multiply(matrix, item.transform);
    if (inner.some((value) => !Number.isFinite(value) || Math.abs(value) > SVG_ASSET_LIMITS.maxMatrixEntry)) {
        refuse(`nested transforms compose beyond ±${SVG_ASSET_LIMITS.maxMatrixEntry}`);
    }
    // Clip shapes and mask content draw under the same transforms, so they
    // are held to the same limit, though only the children add paint.
    for (const shape of item.clip?.shapes ?? []) {
        const clip = multiply(inner, shape.transform);
        if (clip.some((value) => !Number.isFinite(value) || Math.abs(value) > SVG_ASSET_LIMITS.maxMatrixEntry)) {
            refuse(`nested transforms compose beyond ±${SVG_ASSET_LIMITS.maxMatrixEntry}`);
        }
    }
    for (const child of item.mask?.children ?? []) paintBounds(child, inner);
    let box = null;
    for (const child of item.children) {
        const next = paintBounds(child, inner);
        if (!next) continue;
        box = box ? { left: Math.min(box.left, next.left), top: Math.min(box.top, next.top),
            right: Math.max(box.right, next.right), bottom: Math.max(box.bottom, next.bottom) } : next;
    }
    return box;
}

function sourceTreeDepth(root) {
    let deepest = 0;
    const pending = [[root, 1]];
    while (pending.length > 0) {
        const [node, depth] = pending.pop();
        deepest = Math.max(deepest, depth);
        for (const child of node.children ?? []) pending.push([child, depth + 1]);
    }
    return deepest;
}

function longestSourceId(root) {
    let longest = 0;
    const pending = [root];
    while (pending.length > 0) {
        const node = pending.pop();
        if (typeof node.sourceId === 'string') longest = Math.max(longest, utf8Length(node.sourceId));
        for (const child of node.children ?? []) pending.push(child);
    }
    return longest;
}

function validateOptions(options) {
    if (options === null || typeof options !== 'object' || Array.isArray(options)) {
        throw new TypeError('SVG asset options must be an object');
    }
    const sourceId = options.sourceId ?? 'svg';
    if (typeof sourceId !== 'string' || !/^[A-Za-z0-9._:-]{1,128}$/.test(sourceId)) {
        throw new TypeError('sourceId must be 1 to 128 letters, digits, dots, colons, hyphens or underscores');
    }
    const nodeBudget = options.nodeBudget ?? DEFAULT_SVG_NODE_BUDGET;
    const pathDataBudget = options.pathDataBudget ?? DEFAULT_SVG_PATH_DATA_BUDGET;
    if (!Number.isSafeInteger(nodeBudget) || nodeBudget < 1 || nodeBudget > SVG_ASSET_LIMITS.maxNodeBudget ||
        !Number.isSafeInteger(pathDataBudget) || pathDataBudget < 1) {
        throw new TypeError('SVG budgets must be positive integers');
    }
    for (const name of ['width', 'height']) {
        if (options[name] !== undefined && !(Number.isFinite(options[name]) && options[name] > 0)) {
            throw new TypeError(`${name} must be a positive finite number`);
        }
    }
    if (options.rasterize !== undefined) {
        throw new TypeError('rasterize is not supported: features only a rasterizer draws are dropped as losses');
    }
    for (const [color, token] of Object.entries(options.colorTokens ?? {})) {
        if (!/^#[0-9a-f]{6}(?:[0-9a-f]{2})?$/i.test(color) || typeof token !== 'string' ||
            !/^[A-Za-z0-9_.-]+$/.test(token)) {
            throw new TypeError('colorTokens maps #rrggbb colours to token names');
        }
    }
    return { ...options, sourceId, nodeBudget, pathDataBudget };
}

/**
 * Import an SVG asset as DesignIR source nodes.
 *
 * Returns `{ mode, root, diagnostics, assets }`: `mode` is `subtree` or
 * `compiled`; `root` is a source node for `normalizeImport`; `diagnostics`
 * holds one entry per dropped or approximated feature, with the mode
 * decision itself; `assets` is empty, as nothing is rasterized.
 * Hostile input throws `SvgAssetRefusal` before any lowering.
 */
export function importSvgAsset(svg, options = {}) {
    const settings = validateOptions(options);
    const importer = new Importer(svg, settings);
    importer.compiledLimits = [];
    const tree = importer.build();
    const contentHash = sha256(svg);
    const rootLayout = { width: Number(fmt(importer.width)), height: Number(fmt(importer.height)) };
    const fill = { x: 0, y: 0, ...rootLayout };
    // The viewport clips only when something reaches past it, so an asset
    // drawn inside its box keeps a clip-free root that partial redraw can cut.
    const reach = paintBounds(tree);
    const spills = reach !== null && (reach.left < 0 || reach.top < 0 ||
        reach.right > importer.width || reach.bottom > importer.height);
    const root = (children) => ({ kind: 'view', sourceId: settings.sourceId,
        properties: { layout: rootLayout, paint: spills ? { overflow: 'hidden' } : {} }, children });
    const decision = (code, message, disposition = 'lowered', severity = 'info') =>
        importer.report(code, settings.sourceId, message, disposition, severity);

    // Nothing is rasterized: what only a rasterizer could draw is dropped,
    // and each feature dropped is a loss.
    for (const item of importer.rasterOnly) {
        importer.report('svg-raster-feature-dropped', item.path,
            `${item.reason}; this importer does not rasterize, so it was dropped`);
    }

    const counted = countTree(tree);
    const overBudget = counted.nodes + 1 > settings.nodeBudget || counted.bytes > settings.pathDataBudget;
    const subtree = new SubtreeLowering(importer);
    let lowered = null;
    if (!overBudget) {
        lowered = subtree.lower(tree, importer.colorTokens);
        if (lowered && sourceTreeDepth(lowered) + 1 > SVG_ASSET_LIMITS.maxSubtreeDepth) {
            subtree.why(`a subtree deeper than ${SVG_ASSET_LIMITS.maxSubtreeDepth} levels`);
        }
        if (lowered && longestSourceId(lowered) > SVG_ASSET_LIMITS.maxSourceIdBytes) {
            subtree.why(`identities longer than ${SVG_ASSET_LIMITS.maxSourceIdBytes} bytes`);
        }
        if (subtree.nodes + 1 > settings.nodeBudget || subtree.pathBytes > settings.pathDataBudget) {
            lowered = null;
        }
    }
    if (lowered && subtree.blocked.length === 0) {
        return { mode: 'subtree', root: root([lowered]), diagnostics: importer.diagnostics, assets: [] };
    }

    const compiled = new CompiledLowering(importer);
    compiled.lower(tree);
    let depth = 0;
    let deepest = 0;
    for (const op of compiled.ops) {
        if (op.op === 'group') deepest = Math.max(deepest, ++depth);
        else if (op.op === 'end') depth -= 1;
    }
    const compiledBytes = compiled.ops.reduce((sum, op) => sum + utf8Length(op.d ?? op.clip?.d ?? ''), 0);
    if (compiled.ops.length > SVG_ASSET_LIMITS.maxCompiledOps ||
        deepest > SVG_ASSET_LIMITS.maxCompiledDepth ||
        compiledBytes > SVG_ASSET_LIMITS.maxTotalPathDataBytes) {
        refuse(`its compiled form needs ${compiled.ops.length} operations nested ${deepest} deep ` +
            `with ${compiledBytes} bytes of path data, beyond ${SVG_ASSET_LIMITS.maxCompiledOps} operations, ` +
            `${SVG_ASSET_LIMITS.maxCompiledDepth} levels or ${SVG_ASSET_LIMITS.maxTotalPathDataBytes} bytes`);
    }
    for (const limit of importer.compiledLimits) {
        importer.report('svg-compiled-feature-dropped', settings.sourceId,
            `the compiled form cannot carry ${limit}; it was drawn without it`);
    }
    importer.tokensBound.clear();
    const bakedTokens = [...importer.tokenMatches].sort();
    if (bakedTokens.length > 0) {
        decision('svg-compiled-tokens-baked',
            `the compiled resource bakes the colours bound to ${bakedTokens.join(', ')}; ` +
            'changing those tokens does not recolour it', 'unsupported', 'warning');
    }
    const why = overBudget || !lowered
        ? `it exceeds the declared budgets (${counted.nodes + 1} nodes of ${settings.nodeBudget}, ` +
          `${counted.bytes} bytes of path data of ${settings.pathDataBudget})`
        : `it uses ${[...new Set(subtree.blocked)].join(', ')}, which only the compiled form carries`;
    decision('svg-compiled', `the asset became one compiled path resource because ${why}`);
    const asset = { contentHash, viewBox: [0, 0, rootLayout.width, rootLayout.height], ops: compiled.ops };
    return {
        mode: 'compiled',
        root: root([{ kind: 'vector', sourceId: `${settings.sourceId}:compiled`,
            properties: { layout: fill, vector: { asset } }, children: [] }]),
        diagnostics: importer.diagnostics,
        assets: [],
    };
}

/**
 * Import an SVG asset and normalize it into a DesignIR document whose
 * `lossReport` lists every dropped or approximated feature.
 * `source` and `tokens` are as for `normalizeImport`; the other options are
 * those of `importSvgAsset`.
 */
export function svgAssetToDesignIR(svg, { source, tokens, ...options } = {}) {
    const imported = importSvgAsset(svg, { ...options, sourceKey: source?.key });
    const document = normalizeImport({
        source, tokens, root: imported.root, diagnostics: imported.diagnostics,
        assets: imported.assets,
    });
    return { mode: imported.mode, document };
}

export const svgAssetTesting = Object.freeze({
    parsePathData, pathBounds, serializePath, parseColor, parseTransform, sha256,
    // Every scanner that reads text from the asset, for the linear-time test.
    scanners: Object.freeze({
        numberEnd: (text) => numberEnd(text, 0), readUrl: (text) => readUrl(text, 0), leadingUrl,
        urlReferences: (text) => urlReferences(text, 'test'), localReference,
        scanTag: (text) => scanTag(text, 0), splitImportant, unmodelledCss,
        styleDeclarations: (text) => styleDeclarations(text, 'test'),
        checkNumbers: (text) => checkNumbers(text, 'test'), parseLength, parseNumber,
        parseFraction: (text) => parseFraction(text, 0), parseColor, parseTransform, parsePathData,
        decodeText, stripComments, parseXml, urlLike,
    }),
});
