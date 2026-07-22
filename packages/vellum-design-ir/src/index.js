export {
    AUTHORED_OVERLAY_SCHEMA,
    AUTHORED_OVERLAY_VERSION,
    COMPILER_NAME,
    COMPILER_VERSION,
    DESIGN_IR_SCHEMA,
    DESIGN_IR_VERSION,
    DISPOSITIONS,
    REIMPORT_REPORT_SCHEMA,
    REIMPORT_REPORT_VERSION,
    SEVERITIES,
} from './constants.js';
export { buildLossReport, normalizeDiagnostic } from './diagnostics.js';
export { DesignIRConflictError, DesignIRValidationError } from './errors.js';
export {
    assertSourceKey,
    encodeIdentitySegment,
    identitySetFingerprint,
    indexTree,
    namespacedIdentity,
} from './identity.js';
export { assertNoDuplicateIdentities, normalizeImport } from './normalize.js';
export { applyAuthoredOverlay, emptyAuthoredOverlay, resolveReference } from './overlay.js';
export { reimportDesign } from './reimport.js';
export { canonicalize, deepClone, fnv1a32, jsonEqual, stableStringify } from './stable-json.js';
export { diffTokens, normalizeTokens, resolveTokenLayers } from './tokens.js';
export {
    parseAuthoredOverlay,
    parseDesignIR,
    parseReimportReport,
    validateAuthoredOverlay,
    validateDesignIR,
    validateReimportReport,
} from './validate.js';

import { indexTree } from './identity.js';

export function summarizeDesignIR(document) {
    const { index } = indexTree(document.root);
    const identityStrategies = {};
    const kinds = {};
    for (const { node } of index.values()) {
        identityStrategies[node.identity.strategy] =
            (identityStrategies[node.identity.strategy] ?? 0) + 1;
        kinds[node.kind] = (kinds[node.kind] ?? 0) + 1;
    }
    return {
        assets: document.assets.length,
        diagnostics: document.diagnostics.length,
        identityStrategies,
        kinds,
        losses: document.lossReport.lossCount,
        nodes: index.size,
        revision: document.source.revision,
        schemaVersion: document.schemaVersion,
        sourceKey: document.source.key,
        tokens: Object.keys(document.tokens).length,
    };
}
