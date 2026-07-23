import { canonicalize } from './stable-json.js';

export const FIGMA_PLUGIN_FORMAT = '2026.05-figma-plugin-v1';

const GENERIC_NODE_KINDS = Object.freeze({
    button: 'button',
    col: 'view',
    frame: 'view',
    image: 'image',
    input: 'input',
    label: 'text',
    panel: 'view',
    row: 'view',
    text: 'text',
    vector: 'path',
});
const AUDIO_NODE_KINDS = new Set([
    'fader', 'knob', 'meter', 'spectrum', 'waveform', 'xy_pad',
]);
const AUDIO_NODE_FIELDS = new Set([
    'audio_widget', 'default', 'max', 'min',
]);
const AUDIO_ATTRIBUTE_FIELDS = new Set(['binding', 'binding_y', 'units']);
const STYLE_GROUPS = Object.freeze({
    paint: Object.freeze({
        background_color: 'backgroundColor',
        border_color: 'borderColor',
        border_radius: 'borderRadius',
        border_width: 'borderWidth',
        opacity: 'opacity',
    }),
    text: Object.freeze({
        color: 'color',
        font_family: 'fontFamily',
        font_size: 'fontSize',
        font_style: 'fontStyle',
        font_weight: 'fontWeight',
        letter_spacing: 'letterSpacing',
        line_height: 'lineHeight',
        text_align: 'textAlign',
        text_decoration: 'textDecoration',
        text_transform: 'textTransform',
    }),
    layout: Object.freeze({
        bottom: 'bottom',
        height: 'height',
        left: 'left',
        max_height: 'maxHeight',
        max_width: 'maxWidth',
        min_height: 'minHeight',
        min_width: 'minWidth',
        position: 'position',
        right: 'right',
        top: 'top',
        width: 'width',
        z_index: 'zIndex',
    }),
});
const LAYOUT_FIELDS = Object.freeze({
    align: 'align',
    aspect_ratio: 'aspectRatio',
    column_gap: 'columnGap',
    direction: 'direction',
    display: 'display',
    flex_grow: 'flexGrow',
    flex_shrink: 'flexShrink',
    gap: 'gap',
    height_mode: 'heightMode',
    justify: 'justify',
    margin: 'margin',
    padding: 'padding',
    row_gap: 'rowGap',
    width_mode: 'widthMode',
    wrap: 'wrap',
});

/**
 * Decode the credential-free generic subset of the Figma plugin export.
 * This function is pure: revision and snapshot identity are supplied by the
 * caller from the immutable source bytes rather than clocks or provider APIs.
 */
export function decodeFigmaPluginExport(input, options = {}) {
    const envelope = object(input, '$');
    if (envelope.format_version !== FIGMA_PLUGIN_FORMAT) {
        throw new TypeError(`unsupported Figma plugin format '${envelope.format_version ?? ''}'`);
    }
    const provenance = object(envelope.provenance, '$.provenance');
    if (provenance.adapter !== 'figma-plugin') {
        throw new TypeError("$.provenance.adapter must be 'figma-plugin'");
    }
    const sourceUri = requiredString(provenance.source_uri, '$.provenance.source_uri');
    const sourceIdentity = parseSourceUri(sourceUri);
    const sourceKey = requiredString(options.sourceKey, 'options.sourceKey');
    const snapshotHash = requiredString(options.sourceHash, 'options.sourceHash');
    const revision = options.revision ?? `figma-${snapshotHash.replace(/^sha256:/, '').slice(0, 16)}`;
    const diagnostics = [];
    const root = decodeNode(envelope.root, '$.root', diagnostics);

    for (const diagnostic of envelope.diagnostics ?? []) {
        const value = object(diagnostic, '$.diagnostics[]');
        diagnostics.push({
            code: requiredString(value.code, '$.diagnostics[].code'),
            detail: value.kind === undefined ? undefined : { sourceKind: String(value.kind) },
            disposition: sourceDisposition(value.kind),
            message: requiredString(value.message, '$.diagnostics[].message'),
            path: typeof value.path === 'string' && value.path ? value.path : '$',
            severity: sourceSeverity(value.severity),
        });
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
            provenance: {
                exportedAt: provenance.exported_at ?? null,
                originalSchema: envelope.$schema ?? null,
            },
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
    if (AUDIO_NODE_KINDS.has(sourceKind) || [...AUDIO_NODE_FIELDS].some((field) => node[field] !== undefined)) {
        throw new TypeError(`${path} contains audio-widget fields, which Vellum does not accept`);
    }
    const attributes = node.attributes === undefined ? {} : object(node.attributes, `${path}.attributes`);
    if ([...AUDIO_ATTRIBUTE_FIELDS].some((field) => attributes[field] !== undefined)) {
        throw new TypeError(`${path}.attributes contains audio-specific bindings`);
    }
    const kind = GENERIC_NODE_KINDS[sourceKind];
    if (!kind) throw new TypeError(`${path}.type '${sourceKind}' is not in the generic Figma subset`);
    const properties = decodeProperties(node, path, diagnostics);
    const output = {
        children: (node.children ?? []).map((child, index) =>
            decodeNode(child, `${path}.children[${index}]`, diagnostics),
        ),
        extensions: {
            'dev.vellum.figma-plugin.v1': canonicalize({
                assetRef: node.asset_ref ?? null,
                figma: node.figma ?? null,
                renderMode: node.render_mode ?? null,
                svgAssetId: node.svg_asset_id ?? null,
            }),
        },
        kind,
        name: requiredString(node.name, `${path}.name`),
        properties,
        sourceId: requiredString(node.figma_node_id, `${path}.figma_node_id`),
    };
    if (typeof node.content === 'string') output.text = node.content;
    if (sourceKind === 'button') output.role = 'button';
    if (sourceKind === 'input') output.role = 'textbox';
    if (kind === 'image' && typeof node.asset_ref === 'string') {
        output.properties.asset = { id: node.asset_ref };
    }
    return output;
}

function decodeProperties(node, path, diagnostics) {
    const properties = { layout: {}, paint: {}, text: {} };
    const style = node.style === undefined ? {} : object(node.style, `${path}.style`);
    const consumedStyle = new Set();
    for (const [group, mapping] of Object.entries(STYLE_GROUPS)) {
        for (const [source, target] of Object.entries(mapping)) {
            if (style[source] !== undefined) {
                properties[group][target] = style[source];
                consumedStyle.add(source);
            }
        }
    }
    const layout = node.layout === undefined ? {} : object(node.layout, `${path}.layout`);
    const consumedLayout = new Set();
    for (const [source, target] of Object.entries(LAYOUT_FIELDS)) {
        if (layout[source] !== undefined) {
            properties.layout[target] = layout[source];
            consumedLayout.add(source);
        }
    }
    recordUnmapped(style, consumedStyle, `${path}.style`, diagnostics);
    recordUnmapped(layout, consumedLayout, `${path}.layout`, diagnostics);
    for (const key of Object.keys(properties)) {
        if (Object.keys(properties[key]).length === 0) delete properties[key];
    }
    return canonicalize(properties);
}

function recordUnmapped(value, consumed, path, diagnostics) {
    for (const property of Object.keys(value).sort()) {
        if (consumed.has(property)) continue;
        diagnostics.push({
            code: 'figma-property-preserved-not-materialized',
            detail: { sourceValue: value[property] },
            disposition: 'extension',
            message: 'The Figma property is retained in the immutable source snapshot but is not materialized yet',
            path,
            property,
            severity: 'warning',
        });
    }
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
        const asset = object(raw, `$.asset_manifest.assets[${index}]`);
        const output = {
            id: requiredString(asset.asset_id, `$.asset_manifest.assets[${index}].asset_id`),
        };
        if (asset.content_hash !== undefined) output.contentHash = String(asset.content_hash);
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
    if (value === 'unsupported') return 'unsupported';
    if (value === 'rasterized') return 'rasterized';
    if (value === 'fallback_used') return 'lowered';
    return 'extension';
}

function sourceSeverity(value) {
    return ['error', 'info', 'warning'].includes(value) ? value : 'warning';
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
