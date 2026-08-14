import { relative } from 'node:path';
import { indexTree, summarizeDesignIR } from '../src/index.js';
import {
    ASSET_MANIFEST_SCHEMA,
    GENERATED_BINDINGS_SCHEMA,
    GENERATED_COMPONENT_SCHEMA,
    IMPORT_GRAPH_SCHEMA,
    IMPORT_LOCK_SCHEMA,
    IMPORT_REPORT_SCHEMA,
    jsonBytes,
} from './backend-protocol.js';

export function generatedFiles(context) {
    const {
        applied,
        assets,
        captureEnvelope,
        document,
        overlayCreated,
        overlayValue,
        paths,
        project,
        sourceHash,
        sourceKey,
        sourceArtifact,
        sourceName,
        sourceType,
    } = context;
    const files = new Map();
    const componentDefinitions = flattenComponents(document);
    const importGraph = {
        order: [sourceKey],
        schema: IMPORT_GRAPH_SCHEMA,
        sources: {
            [sourceKey]: {
                adapter: sourceType,
                namespace: document.source.namespace,
            },
        },
    };
    const lock = {
        graphVersion: 1,
        schema: IMPORT_LOCK_SCHEMA,
        sources: {
            [sourceKey]: {
                activeRevision: document.source.revision,
                adapter: sourceType,
                adapterVersion: document.source.adapterVersion,
                designIrSchema: document.$schema,
                formatVersion: document.source.formatVersion,
                namespace: document.source.namespace,
                snapshotHash: sourceHash,
                sourceArtifactKind: sourceArtifact.kind,
            },
        },
    };
    const importReport = {
        diagnostics: [...document.diagnostics, ...assets.diagnostics],
        files: {
            aggregateDesignIr: relative(project, paths.aggregateIr),
            assetManifest: relative(project, paths.assetManifest),
            generatedComponents: relative(project, paths.generatedComponents),
            materializedUi: relative(project, paths.materializedUi),
            nodeIds: relative(project, paths.nodeIds),
            normalizedDesignIr: relative(project, paths.sourceIr),
            overlay: relative(project, paths.overlay),
            snapshot: relative(project, paths.snapshotSource),
            sourceArtifact: relative(
                project,
                sourceArtifact.kind === 'pulp-zip' ? paths.snapshotArchive : paths.snapshotSource,
            ),
            ...(captureEnvelope === undefined ? {} : {
                captureEnvelope: relative(project, paths.snapshotCapture),
            }),
            tokens: relative(project, paths.importedTokens),
            resolvedTokenLayers: relative(project, paths.resolvedTokenLayers),
        },
        lossReport: document.lossReport,
        revision: document.source.revision,
        schema: IMPORT_REPORT_SCHEMA,
        source: {
            adapter: sourceType,
            key: sourceKey,
            name: sourceName,
            snapshotHash: sourceHash,
            sourceArtifact,
        },
        summary: summarizeDesignIR(document),
    };

    files.set(paths.importGraph, jsonBytes(importGraph));
    files.set(paths.importLock, jsonBytes(lock));
    files.set(paths.sourceIr, jsonBytes(document));
    files.set(paths.aggregateIr, jsonBytes(document));
    files.set(paths.generatedComponents, jsonBytes({
        components: componentDefinitions,
        revision: document.source.revision,
        schema: GENERATED_COMPONENT_SCHEMA,
        sourceKey,
    }));
    files.set(paths.nodeIds, Buffer.from(nodeIdsTypescript(componentDefinitions), 'utf8'));
    files.set(paths.importedTokens, jsonBytes(document.tokens));
    files.set(paths.resolvedTokenLayers, jsonBytes({
        layers: applied.tokenLayers,
        revision: document.source.revision,
        schema: 'vellum.resolved-token-layers.v1',
        sourceKey,
    }));
    files.set(paths.assetManifest, jsonBytes({
        assets: assets.manifest,
        revision: document.source.revision,
        schema: ASSET_MANIFEST_SCHEMA,
        sourceKey,
    }));
    files.set(paths.materializedUi, jsonBytes(applied.materialized));
    files.set(paths.generatedBindings, jsonBytes({
        bindings: applied.resolvedBindings,
        revision: document.source.revision,
        schema: GENERATED_BINDINGS_SCHEMA,
        sourceKey,
    }));
    files.set(paths.importReport, jsonBytes(importReport));
    files.set(paths.snapshotProvenance, jsonBytes(snapshotProvenance({
        assets: assets.manifest,
        revision: document.source.revision,
        sourceHash,
        sourceKey,
        sourceArtifact,
        sourceName,
        sourceType,
    })));
    if (captureEnvelope !== undefined) files.set(paths.snapshotCapture, captureEnvelope);
    if (overlayCreated) files.set(paths.overlay, jsonBytes(overlayValue));
    return files;
}

export function snapshotProvenance({
    assets,
    revision,
    sourceArtifact,
    sourceHash,
    sourceKey,
    sourceName,
    sourceType,
}) {
    return {
        assets: assets.map((asset) => ({
            contentHash: asset.contentHash ?? null,
            id: asset.id,
            status: asset.status,
            uri: asset.uri ?? null,
        })),
        revision,
        schema: 'vellum.source-snapshot.v1',
        sourceArtifact,
        sourceHash,
        sourceKey,
        sourceName,
        sourceType,
    };
}

function flattenComponents(document) {
    const { index } = indexTree(document.root);
    return [...index.values()]
        .map(({ childIndex, node, parentId }) => ({
            childIds: node.children.map((child) => child.id),
            childIndex,
            id: node.id,
            kind: node.kind,
            name: node.name ?? null,
            parentId,
            role: node.role ?? null,
        }))
        .sort((left, right) => left.id.localeCompare(right.id));
}

function nodeIdsTypescript(components) {
    const ids = components.map((component) => JSON.stringify(component.id));
    return [
        '// Generated by vellum-backend. Do not edit.',
        `export const importedNodeIds = [${ids.join(', ')}] as const;`,
        'export type ImportedNodeId = (typeof importedNodeIds)[number];',
        '',
    ].join('\n');
}
