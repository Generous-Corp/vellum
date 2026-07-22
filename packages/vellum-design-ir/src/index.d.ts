export const DESIGN_IR_SCHEMA: 'https://vellum.dev/schemas/design-ir/v1';
export const AUTHORED_OVERLAY_SCHEMA: 'https://vellum.dev/schemas/design-ir/authored-overlay/v1';
export const REIMPORT_REPORT_SCHEMA: 'https://vellum.dev/schemas/design-ir/reimport-report/v1';
export const DESIGN_IR_VERSION: 1;
export const AUTHORED_OVERLAY_VERSION: 1;
export const REIMPORT_REPORT_VERSION: 1;
export const COMPILER_NAME: '@vellum/design-ir';
export const COMPILER_VERSION: string;

export type JsonPrimitive = null | boolean | number | string;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };
export type ConversionDisposition =
    | 'portable'
    | 'lowered'
    | 'rasterized'
    | 'web-only'
    | 'extension'
    | 'unsupported';
export type DiagnosticSeverity = 'info' | 'warning' | 'error';
export type IdentityStrategy = 'provider' | 'semantic' | 'structural';

export interface DesignDiagnostic {
    code: string;
    disposition: ConversionDisposition;
    message: string;
    path: string;
    severity: DiagnosticSeverity;
    property?: string;
    remediation?: JsonValue;
    sourceLocation?: JsonValue;
    detail?: JsonValue;
}

export interface DesignIdentity {
    strategy: IdentityStrategy;
    sourceId?: string;
    semanticId?: string;
    fingerprint?: string;
    ordinal?: number;
}

export interface DesignNode {
    id: string;
    identity: DesignIdentity;
    kind: string;
    name?: string;
    role?: string;
    text?: string;
    properties: Record<string, JsonValue>;
    extensions?: Record<string, JsonValue>;
    children: DesignNode[];
}

export interface DesignSource {
    key: string;
    namespace: string;
    adapter: string;
    adapterVersion: string;
    formatVersion: string;
    revision: string;
    snapshotHash?: string;
    sourceUri?: string;
    providerFileKey?: string;
    providerNodeId?: string;
    provenance?: JsonValue;
}

export interface DesignToken {
    $type: string;
    $value: JsonValue;
    $description?: string;
    sourcePath: string;
}

export interface DesignIR {
    $schema: typeof DESIGN_IR_SCHEMA;
    schemaVersion: 1;
    compiler: { name: string; version: string };
    source: DesignSource;
    root: DesignNode;
    tokens: Record<string, DesignToken>;
    assets: Array<{
        id: string;
        contentHash?: string;
        mimeType?: string;
        uri?: string;
        height?: number;
        width?: number;
        provenance?: JsonValue;
    }>;
    diagnostics: DesignDiagnostic[];
    lossReport: {
        byDisposition: Record<ConversionDisposition, number>;
        bySeverity: Record<DiagnosticSeverity, number>;
        lossCount: number;
        losses: DesignDiagnostic[];
        totalDiagnostics: number;
    };
}

export interface SourceNode {
    kind?: string;
    sourceId?: string;
    semanticId?: string;
    name?: string;
    role?: string;
    text?: string;
    properties?: Record<string, JsonValue>;
    extensions?: Record<string, JsonValue>;
    losses?: Array<Partial<DesignDiagnostic> & Pick<DesignDiagnostic, 'code' | 'message'>>;
    children?: SourceNode[];
    [adapterField: string]: unknown;
}

export interface ImportSourceModel {
    source: DesignSource;
    root: SourceNode;
    tokens?: Record<string, unknown>;
    assets?: DesignIR['assets'];
    diagnostics?: DesignDiagnostic[];
}

export interface AuthoredBinding {
    nodeId: string;
    event: string;
    action: string;
    [developerMetadata: string]: JsonValue;
}

export interface AuthoredOverride {
    nodeId: string;
    path: `properties.${string}`;
    value: JsonValue;
}

export interface AuthoredOverlay {
    $schema: typeof AUTHORED_OVERLAY_SCHEMA;
    schemaVersion: 1;
    sourceKey: string;
    aliases: Record<string, string>;
    bindings: AuthoredBinding[];
    overrides: AuthoredOverride[];
    semanticTokens: Record<string, JsonValue>;
    themeOverrides: Record<string, JsonValue>;
}

export interface ReimportConflict {
    code: string;
    kind: string;
    message: string;
    nodeId?: string;
    path?: string;
    [detail: string]: unknown;
}

export interface ReimportReport {
    $schema: typeof REIMPORT_REPORT_SCHEMA;
    schemaVersion: 1;
    accepted: boolean;
    sourceKey: string;
    previousRevision: string;
    nextRevision: string;
    authoredOverlay: {
        canonicalFingerprint: string;
        preservedByteForByte: true;
    };
    changes: Record<string, unknown>;
    aliasesApplied: unknown[];
    conflicts: ReimportConflict[];
    heuristicCandidates: unknown[];
    summary: Record<string, number>;
}

export interface OverlayApplication {
    aliasesApplied: unknown[];
    appliedOverrides: unknown[];
    conflicts: ReimportConflict[];
    materialized: DesignIR;
    resolvedBindings: Array<AuthoredBinding & {
        originalNodeId: string;
        resolvedNodeId: string;
    }>;
    tokenLayers: TokenLayers;
}

export interface TokenLayers {
    diagnostics: DesignDiagnostic[];
    primitive: Record<string, JsonValue>;
    semantic: Record<string, JsonValue>;
    theme: Record<string, JsonValue>;
}

export const DISPOSITIONS: readonly ConversionDisposition[];
export const SEVERITIES: readonly DiagnosticSeverity[];
export function canonicalize<T>(value: T): T;
export function stableStringify(value: unknown, options?: { space?: number; newline?: boolean }): string;
export function fnv1a32(value: unknown): string;
export function jsonEqual(left: unknown, right: unknown): boolean;
export function deepClone<T>(value: T): T;
export function assertSourceKey(value: string, field?: string): string;
export function encodeIdentitySegment(value: unknown): string;
export function namespacedIdentity(sourceKey: string, localIdentity: unknown): string;
export function identitySetFingerprint(root: DesignNode): string;
export function indexTree(root: DesignNode): {
    index: Map<string, { node: DesignNode; parentId: string | null; childIndex: number; path: string }>;
    duplicateIds: string[];
};
export function normalizeDiagnostic(value: unknown, fallbackPath?: string): DesignDiagnostic;
export function buildLossReport(diagnostics: DesignDiagnostic[]): DesignIR['lossReport'];
export function normalizeTokens(rawTokens: unknown, sourceKey: string): Record<string, DesignToken>;
export function resolveTokenLayers(document: DesignIR, overlay?: Partial<AuthoredOverlay>): TokenLayers;
export function diffTokens(previous: DesignIR['tokens'], next: DesignIR['tokens']): unknown[];
export function normalizeImport(input: ImportSourceModel, options?: { compilerVersion?: string }): DesignIR;
export function assertNoDuplicateIdentities(document: DesignIR): void;
export function emptyAuthoredOverlay(sourceKey: string): AuthoredOverlay;
export function applyAuthoredOverlay(document: DesignIR, overlay: AuthoredOverlay): OverlayApplication;
export function resolveReference(
    nodeId: string,
    index: Map<string, unknown>,
    aliases: Record<string, string>,
): { ok: boolean; nodeId?: string; aliasPath: Array<{ from: string; to: string }>; reason?: string };
export function reimportDesign(
    previous: DesignIR,
    next: DesignIR,
    overlay: AuthoredOverlay,
    options?: { requireNoNewLosses?: boolean },
): {
    accepted: boolean;
    materialized: DesignIR;
    report: ReimportReport;
    resolvedBindings: OverlayApplication['resolvedBindings'];
    tokenLayers: TokenLayers;
};
export function validateDesignIR(value: unknown, options?: { throwOnError?: boolean }): ValidationResult;
export function validateAuthoredOverlay(value: unknown, options?: { throwOnError?: boolean }): ValidationResult;
export function validateReimportReport(value: unknown, options?: { throwOnError?: boolean }): ValidationResult;
export function parseDesignIR(value: string | DesignIR): DesignIR;
export function parseAuthoredOverlay(value: string | AuthoredOverlay): AuthoredOverlay;
export function parseReimportReport(value: string | ReimportReport): ReimportReport;
export function summarizeDesignIR(document: DesignIR): Record<string, unknown>;

export interface ValidationResult {
    valid: boolean;
    issues: Array<{ code: string; message: string; path: string }>;
}

export class DesignIRValidationError extends Error {
    issues: ValidationResult['issues'];
}
export class DesignIRConflictError extends Error {
    conflicts: ReimportConflict[];
}
