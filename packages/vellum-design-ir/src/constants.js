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
 * The schema version that adds plot bindings on nodes. Version 4 belongs to
 * node fields this build does not read (animation tracks), so a document at
 * that version is read with the fields of version 3. Each version keeps
 * every field of the ones before it: a document is written at the oldest
 * version that carries every field it uses, and a field stays illegal below
 * the version that introduced it.
 */
export const DESIGN_IR_PLOTS_VERSION = 5;
export const DESIGN_IR_VERSIONS = Object.freeze([1, 2, 3, 4, 5]);
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
