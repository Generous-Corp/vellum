export const DESIGN_IR_SCHEMA = 'https://vellum.dev/schemas/design-ir/v1';
export const AUTHORED_OVERLAY_SCHEMA =
    'https://vellum.dev/schemas/design-ir/authored-overlay/v1';
export const REIMPORT_REPORT_SCHEMA =
    'https://vellum.dev/schemas/design-ir/reimport-report/v1';

export const DESIGN_IR_VERSION = 1;
// The DesignIR schema versions this build reads and writes. Versions 2 to 4
// belong to other node fields and add nothing this build reads, so they are
// accepted with the fields of version 1; version 5 adds plot bindings on
// nodes. A document without plots stays version 1.
export const DESIGN_IR_PLOTS_VERSION = 5;
export const SUPPORTED_DESIGN_IR_VERSIONS = Object.freeze([1, 2, 3, 4, 5]);
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
