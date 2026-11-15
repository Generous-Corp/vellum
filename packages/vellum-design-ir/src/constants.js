export const DESIGN_IR_SCHEMA = 'https://vellum.dev/schemas/design-ir/v1';
export const AUTHORED_OVERLAY_SCHEMA =
    'https://vellum.dev/schemas/design-ir/authored-overlay/v1';
export const REIMPORT_REPORT_SCHEMA =
    'https://vellum.dev/schemas/design-ir/reimport-report/v1';

export const DESIGN_IR_VERSION = 1;
/**
 * The schema version that adds the node `compositing` object. A document
 * carries it only when a node uses the object.
 */
export const DESIGN_IR_COMPOSITING_VERSION = 2;
/**
 * The schema version that adds node material references. A document carries
 * it only when a node uses one.
 */
export const DESIGN_IR_MATERIALS_VERSION = 3;
/**
 * The schema version that adds vector assets: path and vector nodes,
 * `properties.path`, `properties.vector`, `properties.image.source`, and the
 * paint keys transform, clipPath and overflow.
 */
export const DESIGN_IR_VECTOR_ASSET_VERSION = 6;
/**
 * Every version this compiler reads. A document is written at the oldest
 * version that carries every field it uses, each version keeps every field of
 * the ones before it, and a field stays illegal below the version that adds
 * it, whatever the document's version.
 */
export const DESIGN_IR_VERSIONS = Object.freeze([1, 2, 3, 4, 5, 6]);
export const DESIGN_IR_LATEST_VERSION = 6;
export const DESIGN_IR_VECTOR_PAINT_KEYS = Object.freeze(['transform', 'clipPath', 'overflow']);
export const AUTHORED_OVERLAY_VERSION = 1;
export const REIMPORT_REPORT_VERSION = 1;
export const COMPILER_NAME = '@vellum/design-ir';
export const COMPILER_VERSION = '0.1.0-experimental.0';

export const DISPOSITIONS = Object.freeze([
    'portable',
    'lowered',
    'rasterized',
    'web-only',
    'extension',
    'unsupported',
]);

export const SEVERITIES = Object.freeze(['info', 'warning', 'error']);

/**
 * Blend modes, named as the renderer's scene spells them. Tier 1 is required
 * of every backend; Tier 2 is required of Skia Graphite and optional elsewhere,
 * where an unsupported mode is drawn as `normal` with a loss diagnostic.
 */
export const BLEND_MODES_TIER_1 = Object.freeze([
    'normal',
    'multiply',
    'screen',
    'overlay',
    'darken',
    'lighten',
    'difference',
    'exclusion',
    'source_over',
    'destination_in',
    'destination_out',
]);

export const BLEND_MODES_TIER_2 = Object.freeze([
    'hue',
    'saturation',
    'color',
    'luminosity',
    'color_dodge',
    'color_burn',
    'hard_light',
    'soft_light',
    'clear',
    'source',
    'destination',
    'source_in',
    'source_out',
    'source_atop',
    'destination_over',
    'destination_atop',
    'xor',
    'plus',
]);

export const BLEND_MODES = Object.freeze([...BLEND_MODES_TIER_1, ...BLEND_MODES_TIER_2]);

export const MASK_MODES = Object.freeze(['alpha', 'luminance']);

/** 1 or 2 for a known blend mode, otherwise 0. */
export function blendModeTier(mode) {
    if (BLEND_MODES_TIER_1.includes(mode)) return 1;
    return BLEND_MODES_TIER_2.includes(mode) ? 2 : 0;
}
