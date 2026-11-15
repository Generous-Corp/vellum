export const DESIGN_IR_SCHEMA = 'https://vellum.dev/schemas/design-ir/v1';
export const AUTHORED_OVERLAY_SCHEMA =
    'https://vellum.dev/schemas/design-ir/authored-overlay/v1';
export const REIMPORT_REPORT_SCHEMA =
    'https://vellum.dev/schemas/design-ir/reimport-report/v1';

export const DESIGN_IR_VERSION = 1;
// The DesignIR schema versions this build reads: every version up to 4.
// Version 2 (blend modes) and version 3 (effects and materials) add no fields
// in this build, so a document at either carries only version 1 fields.
// Version 4 adds animation tracks on nodes. Normalization writes version 4 for
// a document with tracks and version 1 otherwise.
export const DESIGN_IR_TRACKS_VERSION = 4;
export const SUPPORTED_DESIGN_IR_VERSIONS = Object.freeze([1, 2, 3, 4]);
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
