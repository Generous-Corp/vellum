function dependenciesMatch(previous, next) {
    return previous !== null && next !== null &&
        previous.length === next.length &&
        previous.every((value, index) => Object.is(value, next[index]));
}

export function registerEffect(frame, frameId, index, effect, dependencies) {
    if (typeof effect !== 'function') {
        throw new TypeError('useEffect requires an effect function');
    }
    if (dependencies !== undefined && !Array.isArray(dependencies)) {
        throw new TypeError('useEffect dependencies must be an array or undefined');
    }
    const nextDependencies = dependencies === undefined ? null : [...dependencies];
    if (index >= frame.hooks.length) {
        if (frame.established) throw new Error(`hook order changed in ${frameId}`);
        frame.hooks.push({
            kind: 'effect',
            dependencies: undefined,
            cleanup: null,
            effect: null,
            pending: false,
        });
    }
    const record = frame.hooks[index];
    if (record.kind !== 'effect') throw new Error(`hook kind changed in ${frameId}`);
    record.pending = record.dependencies === undefined ||
        !dependenciesMatch(record.dependencies, nextDependencies);
    record.effect = effect;
    record.nextDependencies = nextDependencies;
}

export function cloneEffect(hook) {
    return {
        kind: 'effect',
        dependencies: hook.dependencies,
        cleanup: hook.cleanup,
        effect: hook.effect,
        pending: false,
    };
}

// Carries the committed effect instances of `liveFrames` into `restoredFrames`
// (built from a state snapshot, whose effect hooks are blank). A carried hook
// keeps its committed dependencies and cleanup, so the commit that follows
// reruns it only when its dependencies differ, after its cleanup. Effects that
// have no counterpart in the restored frames are cleaned up by that commit.
export function adoptLiveEffects(liveFrames, restoredFrames) {
    for (const [frameId, restored] of restoredFrames) {
        const live = liveFrames.get(frameId);
        if (!live) continue;
        restored.hooks.forEach((hook, index) => {
            const source = live.hooks[index];
            if (hook.kind !== 'effect' || source?.kind !== 'effect') return;
            restored.hooks[index] = cloneEffect(source);
        });
    }
}

function collectEffectJobs(previousFrames, nextFrames) {
    const jobs = [];
    for (const [frameId, frame] of nextFrames) {
        frame.hooks.forEach((hook, index) => {
            if (hook.kind !== 'effect' || !hook.pending) return;
            jobs.push({
                frameId,
                index,
                effect: hook.effect,
                dependencies: hook.nextDependencies,
            });
            hook.pending = false;
            delete hook.nextDependencies;
        });
    }
    for (const [frameId, frame] of previousFrames) {
        const next = nextFrames.get(frameId);
        frame.hooks.forEach((hook, index) => {
            if (hook.kind !== 'effect') return;
            if (next?.hooks[index]?.kind === 'effect') return;
            jobs.push({ removed: hook });
        });
    }
    return jobs;
}

export function scheduleCommittedEffects(runtime, previousFrames, nextFrames) {
    const jobs = collectEffectJobs(previousFrames, nextFrames);
    if (jobs.length === 0) return;
    Promise.resolve().then(() => {
        for (const job of jobs) {
            if (job.removed) {
                // Read at run time: an earlier job in this batch may have
                // replaced the cleanup the removed hook was committed with.
                const cleanup = job.removed.cleanup;
                job.removed.cleanup = null;
                if (typeof cleanup === 'function') cleanup();
                continue;
            }
            const current = runtime.frames.get(job.frameId)?.hooks[job.index];
            // A later commit replaced this effect; that commit either scheduled
            // its own job or kept the committed instance because its
            // dependencies came back to the committed ones.
            if (!current || current.kind !== 'effect' || current.effect !== job.effect) {
                continue;
            }
            const previousCleanup = current.cleanup;
            current.cleanup = null;
            if (typeof previousCleanup === 'function') previousCleanup();
            const cleanup = job.effect();
            if (cleanup !== undefined && typeof cleanup !== 'function') {
                throw new TypeError('useEffect must return a cleanup function or undefined');
            }
            current.cleanup = cleanup ?? null;
            current.dependencies = job.dependencies;
        }
    });
}
