export const DESIGN_IR_SCHEMA = 'https://vellum.dev/schemas/design-ir/v1';
export const AUTHORED_OVERLAY_SCHEMA =
    'https://vellum.dev/schemas/design-ir/authored-overlay/v1';
export const REIMPORT_REPORT_SCHEMA =
    'https://vellum.dev/schemas/design-ir/reimport-report/v1';

// The newest DesignIR schema version this compiler writes and reads. A
// document is written at the oldest version that carries every field it
// uses, so one without the vector asset fields stays at version 1.
export const DESIGN_IR_VERSION = 6;
export const DESIGN_IR_MIN_VERSION = 1;
// Version 6 adds vector assets: path and vector nodes, `properties.path`,
// `properties.vector`, `properties.image.source`, and the paint keys
// opacity, transform, clipPath, maskMode, isolation and overflow.
export const DESIGN_IR_VECTOR_ASSET_VERSION = 6;
// Versions claimed by fields this build does not define; a document at one
// of them is refused.
export const DESIGN_IR_RESERVED_VERSIONS = Object.freeze({
    2: 'blend modes',
    3: 'effects and materials',
    4: 'animation tracks',
    5: 'plots',
});
export const DESIGN_IR_VECTOR_PAINT_KEYS = Object.freeze([
    'opacity', 'transform', 'clipPath', 'maskMode', 'isolation', 'overflow',
]);
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
