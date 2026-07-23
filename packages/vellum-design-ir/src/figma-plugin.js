import { normalizeSha256ContentHash } from './assets.js';
import { canonicalize } from './stable-json.js';

export const FIGMA_PLUGIN_FORMAT = '2026.05-figma-plugin-v1';
export const FIGMA_PLUGIN_PARSER_VERSION = '0.1.0';
export const FIGMA_PLUGIN_COMPAT_SCHEMA_VERSION = '0.3';

const FIGMA_PLUGIN_SCHEMA = 'https://pulp.dev/schemas/figma-plugin-export-v1.json';

// The current Pulp extractor's audio-free output vocabulary is `frame`, `text`,
// `ellipse`, `image`, and `vector`. Only the `frame` and `text` node kinds map
// directly into Vellum's retained native host today; their individual fields
// are still diagnosed when unsupported. Every other row is accepted only with
// a conversion diagnostic. The additional control/layout spellings are legacy
// compatibility inputs, not claims about current Pulp output.
const NODE_RULES = Object.freeze({
    button: Object.freeze({ kind: 'button', disposition: 'lowered' }),
    col: Object.freeze({ kind: 'view', disposition: 'lowered' }),
    ellipse: Object.freeze({ kind: 'view', disposition: 'lowered' }),
    frame: Object.freeze({ kind: 'view', disposition: 'portable' }),
    image: Object.freeze({ kind: 'view', disposition: 'unsupported' }),
    input: Object.freeze({ kind: 'view', disposition: 'unsupported' }),
    label: Object.freeze({ kind: 'text', disposition: 'lowered' }),
    panel: Object.freeze({ kind: 'view', disposition: 'lowered' }),
    path: Object.freeze({ kind: 'view', disposition: 'unsupported' }),
    row: Object.freeze({ kind: 'view', disposition: 'lowered' }),
    text: Object.freeze({ kind: 'text', disposition: 'portable' }),
    vector: Object.freeze({ kind: 'view', disposition: 'unsupported' }),
});

const AUDIO_NODE_KINDS = new Set([
    'fader', 'knob', 'meter', 'spectrum', 'waveform', 'xy_pad',
]);
const AUDIO_NODE_FIELDS = new Set(['default', 'label', 'max', 'min']);
const AUDIO_ATTRIBUTE_FIELDS = new Set(['binding', 'binding_y', 'units']);

const MATERIALIZED_STYLE_FIELDS = Object.freeze({
    background_color: Object.freeze({ group: 'paint', target: 'backgroundColor' }),
    border_radius: Object.freeze({ group: 'paint', target: 'borderRadius' }),
    color: Object.freeze({ group: 'text', target: 'color' }),
    font_size: Object.freeze({ group: 'text', target: 'fontSize' }),
    height: Object.freeze({ group: 'layout', target: 'height' }),
    left: Object.freeze({ group: 'layout', target: 'x' }),
    top: Object.freeze({ group: 'layout', target: 'y' }),
    width: Object.freeze({ group: 'layout', target: 'width' }),
});
const MATERIALIZED_LAYOUT_FIELDS = Object.freeze({
    direction: 'direction',
    display: 'display',
    gap: 'gap',
});
const NODE_FIELDS = new Set([
    'asset_ref', 'attributes', 'audio_widget', 'children', 'content', 'figma',
    'figma_node_id', 'interactive_elements', 'layout', 'name', 'render_mode',
    'style', 'svg_asset_id', 'type',
    ...AUDIO_NODE_FIELDS,
]);
const ENVELOPE_FIELDS = new Set([
    '$schema', 'asset_manifest', 'compat_schema_version', 'diagnostics',
    'font_family_assets', 'format_version', 'library_manifest', 'parser_version',
    'provenance', 'root', 'tokens',
]);

/**
 * Decode the credential-free, audio-free subset of the current Pulp Figma
 * plugin wire contract. Revision and snapshot identity are supplied from the
 * immutable source bytes, so this function remains pure and reproducible.
 */
export function decodeFigmaPluginExport(input, options = {}) {
    const envelope = object(input, '$');
    exact(envelope.$schema, FIGMA_PLUGIN_SCHEMA, '$.$schema');
    exact(envelope.format_version, FIGMA_PLUGIN_FORMAT, '$.format_version');
    exact(envelope.parser_version, FIGMA_PLUGIN_PARSER_VERSION, '$.parser_version');
    exact(
        envelope.compat_schema_version,
        FIGMA_PLUGIN_COMPAT_SCHEMA_VERSION,
        '$.compat_schema_version',
    );
    const provenance = object(envelope.provenance, '$.provenance');
    exact(provenance.adapter, 'figma-plugin', '$.provenance.adapter');
    const sourceUri = requiredString(provenance.source_uri, '$.provenance.source_uri');
    const sourceIdentity = parseSourceUri(sourceUri);
    const sourceKey = requiredString(options.sourceKey, 'options.sourceKey');
    const snapshotHash = requiredString(options.sourceHash, 'options.sourceHash');
    const revision = options.revision ?? `figma-${snapshotHash.replace(/^sha256:/, '').slice(0, 16)}`;
    const diagnostics = [];

    const rootEnvelope = object(envelope.root, '$.root');
    if (rootEnvelope.name === '<multi-export>') {
        throw new TypeError('multi-selection Figma exports are not supported; export exactly one root frame');
    }
    const root = decodeNode(rootEnvelope, '$.root', diagnostics);

    if (envelope.diagnostics !== undefined && !Array.isArray(envelope.diagnostics)) {
        throw new TypeError('$.diagnostics must be an array');
    }
    for (let index = 0; index < (envelope.diagnostics ?? []).length; index += 1) {
        diagnostics.push(decodeSourceDiagnostic(envelope.diagnostics[index], index));
    }

    const sourceProvenance = {
        compatSchemaVersion: envelope.compat_schema_version,
        exportedAt: provenance.exported_at ?? null,
        originalSchema: envelope.$schema,
        parserVersion: envelope.parser_version,
    };
    if (envelope.library_manifest !== undefined && envelope.library_manifest !== null) {
        sourceProvenance.libraryManifest = canonicalize(envelope.library_manifest);
        diagnostics.push(preservedDiagnostic(
            '$.library_manifest',
            'library_manifest',
            envelope.library_manifest,
            'extension',
        ));
    }
    if (envelope.font_family_assets !== undefined) {
        if (!Array.isArray(envelope.font_family_assets)) {
            throw new TypeError('$.font_family_assets must be an array');
        }
        sourceProvenance.fontFamilyAssets = canonicalize(envelope.font_family_assets);
        if (envelope.font_family_assets.length > 0) {
            diagnostics.push({
                code: 'figma-font-catalog-preserved-not-materialized',
                detail: { count: envelope.font_family_assets.length },
                disposition: 'unsupported',
                message: 'The Figma font catalogue is preserved but the native host does not load it yet',
                path: '$.font_family_assets',
                severity: 'warning',
            });
        }
    }
    const unmappedEnvelopeFields = preservedFields(envelope, ENVELOPE_FIELDS);
    if (Object.keys(unmappedEnvelopeFields).length > 0) {
        sourceProvenance.unmappedEnvelopeFields = unmappedEnvelopeFields;
        for (const [field, value] of Object.entries(unmappedEnvelopeFields)) {
            diagnostics.push(preservedDiagnostic('$', field, value, 'extension'));
        }
    }

    return canonicalize({
        assets: decodeAssets(envelope.asset_manifest),
        diagnostics,
        root,
        source: {
            adapter: 'figma-plugin',
            adapterVersion: requiredString(provenance.version, '$.provenance.version'),
            formatVersion: FIGMA_PLUGIN_FORMAT,
            key: sourceKey,
            namespace: sourceKey,
            providerFileKey: sourceIdentity.fileKey,
            providerNodeId: sourceIdentity.nodeId,
            provenance: sourceProvenance,
            revision,
            snapshotHash,
            sourceUri,
        },
        tokens: decodeTokens(envelope.tokens),
    });
}

function decodeNode(value, path, diagnostics) {
    const node = object(value, path);
    const sourceKind = requiredString(node.type, `${path}.type`).toLowerCase();
    if (AUDIO_NODE_KINDS.has(sourceKind)) rejectAudio(path);
    for (const field of AUDIO_NODE_FIELDS) {
        if (node[field] !== undefined) rejectAudio(`${path}.${field}`);
    }

    const audioWidget = node.audio_widget;
    if (audioWidget !== undefined) {
        if (typeof audioWidget !== 'string' || audioWidget.toLowerCase() !== 'none') {
            rejectAudio(`${path}.audio_widget`);
        }
        diagnostics.push({
            code: 'figma-audio-none-sentinel-ignored',
            detail: { sourceValue: audioWidget },
            disposition: 'lowered',
            message: "The neutral audio_widget:'none' sentinel was ignored without enabling audio behavior",
            path,
            property: 'audio_widget',
            severity: 'info',
        });
    }

    const figma = node.figma === undefined ? {} : object(node.figma, `${path}.figma`);
    if (
        figma.library_widget_kind !== undefined &&
        String(figma.library_widget_kind).toLowerCase() !== 'none'
    ) {
        rejectAudio(`${path}.figma.library_widget_kind`);
    }
    const attributes = node.attributes === undefined ? {} : object(node.attributes, `${path}.attributes`);
    for (const field of AUDIO_ATTRIBUTE_FIELDS) {
        if (attributes[field] !== undefined) rejectAudio(`${path}.attributes.${field}`);
    }

    const rule = NODE_RULES[sourceKind];
    if (!rule) throw new TypeError(`${path}.type '${sourceKind}' is not in the pinned Figma subset`);
    if (node.children !== undefined && !Array.isArray(node.children)) {
        throw new TypeError(`${path}.children must be an array`);
    }

    const { properties, unmaterializedLayout, unmaterializedStyle } =
        decodeProperties(node, path, diagnostics);
    if (
        sourceKind === 'ellipse' &&
        properties.paint?.borderRadius === undefined &&
        Number.isFinite(properties.layout?.width) &&
        Number.isFinite(properties.layout?.height)
    ) {
        properties.paint ??= {};
        properties.paint.borderRadius = Math.min(
            properties.layout.width,
            properties.layout.height,
        ) / 2;
    }

    if (rule.disposition !== 'portable') {
        diagnostics.push({
            code: rule.disposition === 'unsupported'
                ? 'figma-node-preserved-not-materialized'
                : 'figma-node-kind-lowered',
            detail: { materializedKind: rule.kind, sourceKind },
            disposition: rule.disposition,
            message: rule.disposition === 'unsupported'
                ? `Figma '${sourceKind}' is preserved as a non-faithful view placeholder`
                : `Figma '${sourceKind}' is lowered to Vellum '${rule.kind}'`,
            path,
            property: 'type',
            severity: 'warning',
        });
    }

    const sourceFields = preservedFields(node, NODE_FIELDS);
    for (const [field, fieldValue] of Object.entries({
        ...sourceFields,
        ...(Object.keys(attributes).length > 0 ? { attributes } : {}),
        ...(node.interactive_elements !== undefined
            ? { interactive_elements: node.interactive_elements } : {}),
        ...(node.render_mode !== undefined ? { render_mode: node.render_mode } : {}),
        ...(node.svg_asset_id !== undefined ? { svg_asset_id: node.svg_asset_id } : {}),
    })) {
        diagnostics.push(preservedDiagnostic(
            path,
            field,
            fieldValue,
            ['interactive_elements', 'render_mode', 'svg_asset_id'].includes(field)
                ? 'unsupported' : 'extension',
        ));
    }

    const extension = {
        assetRef: node.asset_ref ?? null,
        audioWidgetSentinel: audioWidget ?? null,
        figma: Object.keys(figma).length > 0 ? canonicalize(figma) : null,
        sourceFields: Object.keys(sourceFields).length > 0 ? sourceFields : null,
        sourceKind,
        unmaterialized: {
            attributes: Object.keys(attributes).length > 0 ? canonicalize(attributes) : null,
            interactiveElements: node.interactive_elements ?? null,
            layout: Object.keys(unmaterializedLayout).length > 0 ? unmaterializedLayout : null,
            renderMode: node.render_mode ?? null,
            style: Object.keys(unmaterializedStyle).length > 0 ? unmaterializedStyle : null,
            svgAssetId: node.svg_asset_id ?? null,
        },
    };
    const output = {
        children: (node.children ?? []).map((child, index) =>
            decodeNode(child, `${path}.children[${index}]`, diagnostics),
        ),
        extensions: { 'dev.vellum.figma-plugin.v1': canonicalize(extension) },
        kind: rule.kind,
        name: requiredString(node.name, `${path}.name`),
        properties: canonicalize(properties),
        sourceId: requiredString(node.figma_node_id, `${path}.figma_node_id`),
    };
    if (typeof node.content === 'string') output.text = node.content;
    if (rule.kind === 'button') output.role = 'button';
    return output;
}

function decodeProperties(node, path, diagnostics) {
    const properties = { layout: {}, paint: {}, text: {} };
    const style = node.style === undefined ? {} : object(node.style, `${path}.style`);
    const consumedStyle = new Set();
    for (const [source, rule] of Object.entries(MATERIALIZED_STYLE_FIELDS)) {
        if (style[source] !== undefined) {
            properties[rule.group][rule.target] = style[source];
            consumedStyle.add(source);
        }
    }
    const layout = node.layout === undefined ? {} : object(node.layout, `${path}.layout`);
    const consumedLayout = new Set();
    for (const [source, target] of Object.entries(MATERIALIZED_LAYOUT_FIELDS)) {
        if (layout[source] !== undefined) {
            properties.layout[target] = layout[source];
            consumedLayout.add(source);
        }
    }
    if (layout.padding !== undefined) {
        const uniformPadding = decodeUniformPadding(layout.padding);
        if (uniformPadding !== null) {
            properties.layout.padding = uniformPadding;
            consumedLayout.add('padding');
        }
    }
    const unmaterializedStyle = recordUnmapped(style, consumedStyle, `${path}.style`, diagnostics);
    const unmaterializedLayout = recordUnmapped(layout, consumedLayout, `${path}.layout`, diagnostics);
    for (const key of Object.keys(properties)) {
        if (Object.keys(properties[key]).length === 0) delete properties[key];
    }
    return {
        properties: canonicalize(properties),
        unmaterializedLayout: canonicalize(unmaterializedLayout),
        unmaterializedStyle: canonicalize(unmaterializedStyle),
    };
}

function decodeUniformPadding(value) {
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    const sides = ['top', 'right', 'bottom', 'left'].map((side) => value[side]);
    if (sides.every((side) => typeof side === 'number' && Number.isFinite(side)) &&
        sides.every((side) => side === sides[0])) {
        return sides[0];
    }
    return null;
}

function recordUnmapped(value, consumed, path, diagnostics) {
    const preserved = {};
    for (const property of Object.keys(value).sort()) {
        if (consumed.has(property)) continue;
        preserved[property] = canonicalize(value[property]);
        diagnostics.push({
            code: 'figma-property-preserved-not-materialized',
            detail: { sourceValue: value[property] },
            disposition: 'unsupported',
            message: 'The Figma property is preserved in DesignIR but is not materialized by the native host',
            path,
            property,
            severity: 'warning',
        });
    }
    return preserved;
}

function preservedDiagnostic(path, property, value, disposition) {
    return {
        code: 'figma-source-field-preserved-not-materialized',
        detail: { sourceValue: canonicalize(value) },
        disposition,
        message: 'The Figma source field is preserved in DesignIR but is not materialized',
        path,
        property,
        severity: disposition === 'unsupported' ? 'warning' : 'info',
    };
}

function decodeSourceDiagnostic(raw, index) {
    const path = `$.diagnostics[${index}]`;
    const value = object(raw, path);
    const kind = requiredString(value.kind, `${path}.kind`);
    const knownFields = new Set([
        'anchor_id', 'code', 'kind', 'message', 'path', 'property', 'severity',
    ]);
    const detail = { sourceKind: kind };
    if (value.anchor_id !== undefined) detail.sourceAnchorId = String(value.anchor_id);
    const extra = preservedFields(value, knownFields);
    if (Object.keys(extra).length > 0) detail.sourceFields = extra;
    const output = {
        code: requiredString(value.code, `${path}.code`),
        detail,
        disposition: sourceDisposition(kind),
        message: requiredString(value.message, `${path}.message`),
        path: typeof value.path === 'string' && value.path ? value.path : '$',
        severity: sourceSeverity(value.severity, `${path}.severity`),
    };
    if (value.property !== undefined) output.property = String(value.property);
    return output;
}

function decodeTokens(value) {
    if (value === undefined) return {};
    const tokens = object(value, '$.tokens');
    const output = {};
    for (const [name, tokenValue] of Object.entries(tokens.colors ?? {})) {
        output[`color.${name}`] = { $type: 'color', $value: tokenValue };
    }
    for (const [name, tokenValue] of Object.entries(tokens.dimensions ?? {})) {
        output[`dimension.${name}`] = {
            $type: 'dimension',
            $value: { unit: 'px', value: tokenValue },
        };
    }
    for (const [name, tokenValue] of Object.entries(tokens.strings ?? {})) {
        output[`string.${name}`] = { $type: 'string', $value: tokenValue };
    }
    return output;
}

function decodeAssets(value) {
    if (value === undefined) return [];
    const manifest = object(value, '$.asset_manifest');
    if (manifest.version !== 1 || !Array.isArray(manifest.assets)) {
        throw new TypeError('$.asset_manifest must be a version 1 asset manifest');
    }
    return manifest.assets.map((raw, index) => {
        const path = `$.asset_manifest.assets[${index}]`;
        const asset = object(raw, path);
        const output = { id: requiredString(asset.asset_id, `${path}.asset_id`) };
        if (asset.content_hash !== undefined) {
            output.contentHash = normalizeSha256ContentHash(asset.content_hash, `${path}.content_hash`);
        }
        if (asset.mime !== undefined) output.mimeType = String(asset.mime);
        if (asset.local_path !== undefined) output.uri = String(asset.local_path);
        if (asset.width !== undefined) output.width = asset.width;
        if (asset.height !== undefined) output.height = asset.height;
        output.provenance = {
            originalUri: asset.original_uri ?? null,
            originalUriAliases: asset.original_uri_aliases ?? [],
        };
        return output;
    });
}

function parseSourceUri(value) {
    const match = /^figma:\/\/([^/]+)\/(.+)$/.exec(value);
    if (!match) throw new TypeError('$.provenance.source_uri must be a figma:// file/node URI');
    return { fileKey: match[1], nodeId: match[2] };
}

function sourceDisposition(value) {
    if (value === 'fallback_used') return 'lowered';
    if (value === 'rasterized') return 'rasterized';
    if ([
        'capture_partial', 'recognition_unavailable', 'unknown',
        'unresolved_asset', 'unsupported_node', 'unsupported_property',
    ].includes(value)) return 'unsupported';
    throw new TypeError(`unsupported Figma diagnostic kind '${value}'`);
}

function sourceSeverity(value, path) {
    if (!['error', 'info', 'warning'].includes(value)) {
        throw new TypeError(`${path} must be 'error', 'info', or 'warning'`);
    }
    return value;
}

function preservedFields(value, known) {
    const output = {};
    for (const field of Object.keys(value).sort()) {
        if (!known.has(field)) output[field] = canonicalize(value[field]);
    }
    return output;
}

function rejectAudio(path) {
    throw new TypeError(`${path} contains audio-widget data, which Vellum does not accept`);
}

function exact(value, expected, path) {
    if (value !== expected) throw new TypeError(`${path} must equal '${expected}'`);
}

function object(value, path) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
        throw new TypeError(`${path} must be an object`);
    }
    return value;
}

function requiredString(value, path) {
    if (typeof value !== 'string' || !value.trim()) {
        throw new TypeError(`${path} must be a non-empty string`);
    }
    return value.trim();
}
