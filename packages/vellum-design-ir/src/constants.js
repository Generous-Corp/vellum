export const DESIGN_IR_SCHEMA = 'https://vellum.dev/schemas/design-ir/v1';
export const AUTHORED_OVERLAY_SCHEMA =
    'https://vellum.dev/schemas/design-ir/authored-overlay/v1';
export const REIMPORT_REPORT_SCHEMA =
    'https://vellum.dev/schemas/design-ir/reimport-report/v1';

export const DESIGN_IR_VERSION = 1;
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
