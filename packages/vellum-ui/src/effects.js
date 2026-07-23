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

function collectEffectJobs(previousFrames, nextFrames) {
    const jobs = [];
    for (const [frameId, frame] of nextFrames) {
        frame.hooks.forEach((hook, index) => {
            if (hook.kind !== 'effect' || !hook.pending) return;
            jobs.push({
                frameId,
                index,
                cleanup: hook.cleanup,
                effect: hook.effect,
                dependencies: hook.nextDependencies,
            });
            hook.pending = false;
            delete hook.nextDependencies;
        });
    }
    for (const [frameId, frame] of previousFrames) {
        if (nextFrames.has(frameId)) continue;
        for (const hook of frame.hooks) {
            if (hook.kind === 'effect' && typeof hook.cleanup === 'function') {
                jobs.push({ cleanup: hook.cleanup, removed: true });
            }
        }
    }
    return jobs;
}

export function scheduleCommittedEffects(runtime, previousFrames, nextFrames) {
    const jobs = collectEffectJobs(previousFrames, nextFrames);
    if (jobs.length === 0) return;
    Promise.resolve().then(() => {
        for (const job of jobs) {
            if (typeof job.cleanup === 'function') job.cleanup();
            if (job.removed) continue;
            const current = runtime.frames.get(job.frameId)?.hooks[job.index];
            if (!current || current.kind !== 'effect' || current.effect !== job.effect) {
                continue;
            }
            const cleanup = job.effect();
            if (cleanup !== undefined && typeof cleanup !== 'function') {
                throw new TypeError('useEffect must return a cleanup function or undefined');
            }
            current.cleanup = cleanup ?? null;
            current.dependencies = job.dependencies;
        }
    });
}
