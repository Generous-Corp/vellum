import {
    REIMPORT_REPORT_SCHEMA,
    REIMPORT_REPORT_VERSION,
} from './constants.js';
import { indexTree, normalizeText, structuralSignature } from './identity.js';
import { applyAuthoredOverlay } from './overlay.js';
import { canonicalize, fnv1a32, jsonEqual } from './stable-json.js';
import { diffTokens } from './tokens.js';
import { validateAuthoredOverlay, validateDesignIR } from './validate.js';

/**
 * Overlay-aware reimport. It compares two generated documents, resolves only
 * exact identities and reviewed aliases, reapplies authored behavior, and
 * reports heuristic candidates without ever accepting them automatically.
 */
export function reimportDesign(previous, next, authoredOverlay, options = {}) {
    validateDesignIR(previous, { throwOnError: true });
    validateDesignIR(next, { throwOnError: true });
    validateAuthoredOverlay(authoredOverlay, { throwOnError: true });
    assertCompatibleSources(previous, next, authoredOverlay);

    const previousIndex = indexTree(previous.root).index;
    const nextIndex = indexTree(next.root).index;
    const changes = diffTrees(previousIndex, nextIndex);
    const overlayResult = applyAuthoredOverlay(next, authoredOverlay);
    const heuristicCandidates = findHeuristicCandidates(
        previousIndex,
        nextIndex,
        new Set(changes.removed.map((item) => item.id)),
        new Set(changes.added.map((item) => item.id)),
        authoredOverlay.aliases,
    );

    const conflicts = [
        ...validateAliasGraph(previousIndex, nextIndex, authoredOverlay.aliases),
        ...overlayResult.conflicts,
    ];
    if (options.requireNoNewLosses !== false) {
        const previousLosses = new Set(
            previous.diagnostics
                .filter(isHardLoss)
                .map((item) => `${item.code}\u0000${item.path}\u0000${item.property ?? ''}`),
        );
        for (const diagnostic of next.diagnostics.filter(isHardLoss)) {
            const key = `${diagnostic.code}\u0000${diagnostic.path}\u0000${diagnostic.property ?? ''}`;
            if (!previousLosses.has(key)) {
                conflicts.push({
                    code: 'new-conversion-loss',
                    diagnostic,
                    kind: 'conversion',
                    message: `Reimport introduced ${diagnostic.disposition} content at ${diagnostic.path}`,
                });
            }
        }
    }

    const report = canonicalize({
        $schema: REIMPORT_REPORT_SCHEMA,
        accepted: conflicts.length === 0,
        aliasesApplied: overlayResult.aliasesApplied,
        authoredOverlay: {
            canonicalFingerprint: `fnv1a32:${fnv1a32(authoredOverlay)}`,
            preservedByteForByte: true,
        },
        changes: {
            ...changes,
            aliases: aliasChanges(previousIndex, nextIndex, authoredOverlay.aliases),
            tokens: diffTokens(previous.tokens, next.tokens),
        },
        conflicts: conflicts.sort(conflictSort),
        heuristicCandidates,
        nextRevision: next.source.revision,
        previousRevision: previous.source.revision,
        schemaVersion: REIMPORT_REPORT_VERSION,
        sourceKey: next.source.key,
        summary: {
            added: changes.added.length,
            aliasesApplied: overlayResult.aliasesApplied.length,
            changed: changes.changed.length,
            conflicts: conflicts.length,
            heuristicCandidates: heuristicCandidates.length,
            moved: changes.moved.length,
            removed: changes.removed.length,
            renamed: changes.renamed.length,
            retained: changes.retained.length,
            tokenChanges: diffTokens(previous.tokens, next.tokens).length,
        },
    });

    return {
        accepted: report.accepted,
        materialized: overlayResult.materialized,
        report,
        resolvedBindings: overlayResult.resolvedBindings,
        tokenLayers: overlayResult.tokenLayers,
    };
}

function assertCompatibleSources(previous, next, overlay) {
    const fields = ['key', 'namespace', 'adapter'];
    for (const field of fields) {
        if (previous.source[field] !== next.source[field]) {
            throw new TypeError(
                `reimport cannot change source.${field} from '${previous.source[field]}' ` +
                    `to '${next.source[field]}'`,
            );
        }
    }
    if (overlay.sourceKey !== next.source.key) {
        throw new TypeError(`overlay sourceKey '${overlay.sourceKey}' does not match reimport source`);
    }
}

function diffTrees(previousIndex, nextIndex) {
    const added = [];
    const removed = [];
    const retained = [];
    const moved = [];
    const renamed = [];
    const changed = [];

    for (const [id, nextEntry] of nextIndex) {
        const previousEntry = previousIndex.get(id);
        if (!previousEntry) {
            added.push(nodeReceipt(nextEntry));
            continue;
        }
        retained.push({ id });
        if (
            previousEntry.parentId !== nextEntry.parentId ||
            previousEntry.childIndex !== nextEntry.childIndex
        ) {
            moved.push({
                from: { index: previousEntry.childIndex, parentId: previousEntry.parentId },
                id,
                to: { index: nextEntry.childIndex, parentId: nextEntry.parentId },
            });
        }
        if (previousEntry.node.name !== nextEntry.node.name) {
            renamed.push({
                from: previousEntry.node.name ?? null,
                id,
                to: nextEntry.node.name ?? null,
            });
        }
        const previousComparable = comparableNode(previousEntry.node);
        const nextComparable = comparableNode(nextEntry.node);
        if (!jsonEqual(previousComparable, nextComparable)) {
            changed.push({
                fields: changedFields(previousComparable, nextComparable),
                id,
            });
        }
    }

    for (const [id, previousEntry] of previousIndex) {
        if (!nextIndex.has(id)) removed.push(nodeReceipt(previousEntry));
    }
    return Object.fromEntries(
        Object.entries({ added, changed, moved, removed, renamed, retained }).map(([key, value]) => [
            key,
            value.sort((left, right) => left.id.localeCompare(right.id)),
        ]),
    );
}

function nodeReceipt(entry) {
    return {
        id: entry.node.id,
        kind: entry.node.kind,
        name: entry.node.name ?? null,
        parentId: entry.parentId,
    };
}

function comparableNode(node) {
    return {
        extensions: node.extensions,
        kind: node.kind,
        properties: node.properties,
        role: node.role,
        text: node.text,
    };
}

function changedFields(previous, next) {
    const output = [];
    collectChangedPaths(previous, next, '', output);
    return output.sort();
}

function collectChangedPaths(previous, next, prefix, output) {
    if (jsonEqual(previous, next)) return;
    if (!isPlainObject(previous) || !isPlainObject(next)) {
        output.push(prefix || '$');
        return;
    }
    const fields = new Set([...Object.keys(previous), ...Object.keys(next)]);
    for (const field of [...fields].sort()) {
        collectChangedPaths(
            previous[field],
            next[field],
            prefix ? `${prefix}.${field}` : field,
            output,
        );
    }
}

function isPlainObject(value) {
    return Boolean(value && typeof value === 'object' && !Array.isArray(value));
}

function aliasChanges(previousIndex, nextIndex, aliases) {
    const output = [];
    for (const [from, to] of Object.entries(aliases).sort()) {
        output.push({
            from,
            previousExists: previousIndex.has(from),
            status: nextIndex.has(to) ? 'approved' : 'target-missing',
            to,
        });
    }
    return output;
}

function validateAliasGraph(previousIndex, nextIndex, aliases) {
    const conflicts = [];
    const intermediateIds = new Set(Object.values(aliases));
    for (const from of Object.keys(aliases).sort()) {
        if (!previousIndex.has(from) && !intermediateIds.has(from)) {
            conflicts.push({
                code: 'alias-source-missing',
                kind: 'alias',
                message: `Reviewed alias source '${from}' does not exist in the previous design`,
                nodeId: from,
            });
        }
        const visited = new Set([from]);
        let current = from;
        while (Object.hasOwn(aliases, current)) {
            current = aliases[current];
            if (visited.has(current)) {
                conflicts.push({
                    code: 'alias-cycle',
                    kind: 'alias',
                    message: `Reviewed alias chain beginning at '${from}' contains a cycle`,
                    nodeId: from,
                });
                current = null;
                break;
            }
            visited.add(current);
        }
        if (current !== null && !nextIndex.has(current)) {
            conflicts.push({
                code: 'alias-target-missing',
                kind: 'alias',
                message: `Reviewed alias '${from}' resolves to missing identity '${current}'`,
                nodeId: from,
                targetNodeId: current,
            });
        }
    }
    return conflicts;
}

function findHeuristicCandidates(previousIndex, nextIndex, removed, added, aliases) {
    const aliasedOld = new Set(Object.keys(aliases));
    const aliasedNew = new Set(Object.values(aliases));
    const output = [];
    for (const oldId of [...removed].sort()) {
        if (aliasedOld.has(oldId)) continue;
        const oldEntry = previousIndex.get(oldId);
        const candidates = [];
        for (const newId of [...added].sort()) {
            if (aliasedNew.has(newId)) continue;
            const nextEntry = nextIndex.get(newId);
            const score = heuristicScore(oldEntry, nextEntry);
            if (score >= 0.55) {
                candidates.push({
                    id: newId,
                    score: Number(score.toFixed(3)),
                });
            }
        }
        candidates.sort((left, right) => right.score - left.score || left.id.localeCompare(right.id));
        if (candidates.length === 0) continue;
        const ambiguous = candidates.length > 1 && candidates[0].score - candidates[1].score <= 0.05;
        output.push({
            ambiguous,
            candidates,
            oldId,
            requiresReview: true,
        });
    }
    return output;
}

function heuristicScore(oldEntry, nextEntry) {
    const oldNode = oldEntry.node;
    const nextNode = nextEntry.node;
    let score = 0;
    if (oldNode.kind === nextNode.kind) score += 0.4;
    if (normalizeText(oldNode.name) && normalizeText(oldNode.name) === normalizeText(nextNode.name)) {
        score += 0.25;
    }
    if (normalizeText(oldNode.role) && normalizeText(oldNode.role) === normalizeText(nextNode.role)) {
        score += 0.15;
    }
    if (normalizeText(oldNode.text) && normalizeText(oldNode.text) === normalizeText(nextNode.text)) {
        score += 0.15;
    }
    if (oldEntry.parentId && oldEntry.parentId === nextEntry.parentId) score += 0.05;
    // Same structure but changed visible text is useful evidence, never truth.
    if (jsonEqual(structuralSignature(oldNode), structuralSignature(nextNode))) score += 0.05;
    return Math.min(score, 1);
}

function isHardLoss(diagnostic) {
    return ['rasterized', 'web-only', 'unsupported'].includes(diagnostic.disposition);
}

function conflictSort(left, right) {
    return `${left.kind}\u0000${left.nodeId ?? left.diagnostic?.path ?? ''}\u0000${left.code}`.localeCompare(
        `${right.kind}\u0000${right.nodeId ?? right.diagnostic?.path ?? ''}\u0000${right.code}`,
    );
}
