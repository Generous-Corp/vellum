import { randomUUID } from 'node:crypto';
import {
    lstat,
    mkdir,
    readFile,
    readdir,
    rename,
    rm,
    rmdir,
    writeFile,
} from 'node:fs/promises';
import { hostname } from 'node:os';
import { basename, join } from 'node:path';
import process from 'node:process';

const OWNER_SCHEMA = 'vellum.import-operation-lock.v1';
const WAIT_MS = 60_000;
const POLL_MS = 25;
const ORPHAN_GRACE_MS = 60_000;

function lockError(status, message) {
    const error = new Error(message);
    error.status = status;
    return error;
}

function pathsFor(project) {
    const root = join(project, '.vellum', 'locks');
    const lock = join(root, 'design-import.lock');
    return { lock, owner: join(lock, 'owner.json'), root };
}

function validOwner(value) {
    return Boolean(
        value &&
        typeof value === 'object' &&
        !Array.isArray(value) &&
        value.schema === OWNER_SCHEMA &&
        Number.isSafeInteger(value.pid) &&
        value.pid > 0 &&
        typeof value.hostname === 'string' &&
        value.hostname.length > 0 &&
        typeof value.nonce === 'string' &&
        /^[0-9a-f-]{36}$/i.test(value.nonce) &&
        typeof value.acquiredAt === 'string' &&
        Number.isFinite(Date.parse(value.acquiredAt))
    );
}

function processIsAlive(pid) {
    try {
        process.kill(pid, 0);
        return true;
    } catch (error) {
        return error?.code !== 'ESRCH';
    }
}

function sameIdentity(left, right) {
    return Boolean(left && right && left.dev === right.dev && left.ino === right.ino);
}

async function inspect(project, paths, assertSafePath) {
    await assertSafePath(project, paths.owner);
    const lockMetadata = await lstat(paths.lock).catch((error) => {
        if (error.code === 'ENOENT') return null;
        throw error;
    });
    if (lockMetadata === null) return { exists: false };
    if (lockMetadata.isSymbolicLink() || !lockMetadata.isDirectory()) {
        throw lockError('unsafe_import_lock', `Import lock is not a real directory: ${paths.lock}`);
    }
    const ownerMetadata = await lstat(paths.owner).catch((error) => {
        if (error.code === 'ENOENT') return null;
        throw error;
    });
    if (
        ownerMetadata !== null &&
        (ownerMetadata.isSymbolicLink() || !ownerMetadata.isFile() ||
            ownerMetadata.size > 16 * 1024)
    ) {
        throw lockError(
            'unsafe_import_lock',
            `Import lock owner is not a bounded regular file: ${paths.owner}`,
        );
    }
    const ownerBytes = ownerMetadata === null ? null : await readFile(paths.owner);
    let ownerValue = null;
    if (ownerBytes !== null) {
        try {
            ownerValue = JSON.parse(ownerBytes.toString('utf8'));
        } catch {
            ownerValue = null;
        }
    }
    return { exists: true, lockMetadata, ownerBytes, ownerMetadata, ownerValue };
}

function recoverable(observation, now = Date.now()) {
    if (!observation.exists) return false;
    if (validOwner(observation.ownerValue)) {
        return observation.ownerValue.hostname === hostname() &&
            !processIsAlive(observation.ownerValue.pid);
    }
    return now - observation.lockMetadata.mtimeMs >= ORPHAN_GRACE_MS;
}

async function recover(project, paths, observation, assertSafePath) {
    if (!recoverable(observation)) return false;
    const nonce = randomUUID();
    const claim = join(paths.lock, `.recovery-${nonce}`);
    const tombstone = join(paths.root, `.design-import.recovered-${nonce}`);
    await assertSafePath(project, claim);
    await assertSafePath(project, join(tombstone, '.probe'));
    const hadOwner = observation.ownerMetadata !== null;
    let claimed = false;
    let detached = false;
    try {
        if (hadOwner) {
            await rename(paths.owner, claim);
            claimed = true;
            if (!(await readFile(claim)).equals(observation.ownerBytes)) {
                await rename(claim, paths.owner).catch(() => {});
                return false;
            }
        } else {
            await writeFile(claim, nonce, { flag: 'wx', mode: 0o600 });
            claimed = true;
        }
        const current = await lstat(paths.lock);
        const entries = await readdir(paths.lock);
        if (
            !sameIdentity(observation.lockMetadata, current) ||
            entries.length !== 1 ||
            entries[0] !== basename(claim)
        ) {
            if (hadOwner) await rename(claim, paths.owner).catch(() => {});
            else await rm(claim, { force: true }).catch(() => {});
            return false;
        }
        await rename(paths.lock, tombstone);
        detached = true;
        await rm(tombstone, { force: true, recursive: true });
        return true;
    } catch (error) {
        if (claimed && !detached) {
            if (hadOwner) await rename(claim, paths.owner).catch(() => {});
            else await rm(claim, { force: true }).catch(() => {});
        }
        if (error.code === 'ENOENT' || error.code === 'EEXIST') return false;
        throw lockError(
            'import_lock_recovery_failed',
            `Could not recover stale import lock: ${error.message}`,
        );
    }
}

export async function acquireImportOperationLock(project, assertSafePath) {
    const paths = pathsFor(project);
    await assertSafePath(project, join(paths.root, '.probe'));
    await mkdir(paths.root, { recursive: true });
    const rootMetadata = await lstat(paths.root);
    if (rootMetadata.isSymbolicLink() || !rootMetadata.isDirectory()) {
        throw lockError(
            'unsafe_import_lock',
            `Import lock root is not a real directory: ${paths.root}`,
        );
    }
    const deadline = Date.now() + WAIT_MS;
    while (true) {
        const nonce = randomUUID();
        const ownerBytes = Buffer.from(`${JSON.stringify({
            acquiredAt: new Date().toISOString(),
            hostname: hostname(),
            nonce,
            pid: process.pid,
            schema: OWNER_SCHEMA,
        })}\n`);
        try {
            await mkdir(paths.lock);
            try {
                await writeFile(paths.owner, ownerBytes, { flag: 'wx', mode: 0o600 });
            } catch (error) {
                await rm(paths.owner, { force: true }).catch(() => {});
                await rmdir(paths.lock).catch(() => {});
                throw error;
            }
            return { nonce, ownerBytes, paths };
        } catch (error) {
            if (error.code !== 'EEXIST') {
                throw lockError(
                    'import_lock_failed',
                    `Could not acquire import lock: ${error.message}`,
                );
            }
        }
        const observation = await inspect(project, paths, assertSafePath);
        if (!observation.exists || await recover(project, paths, observation, assertSafePath)) {
            continue;
        }
        if (Date.now() >= deadline) {
            throw lockError(
                'import_lock_busy',
                'Another Vellum import or reimport is already running for this project',
            );
        }
        await new Promise((resolvePromise) => setTimeout(resolvePromise, POLL_MS));
    }
}

export async function releaseImportOperationLock(project, lease, assertSafePath) {
    const { paths } = lease;
    const observation = await inspect(project, paths, assertSafePath);
    if (
        !observation.exists ||
        observation.ownerBytes === null ||
        !observation.ownerBytes.equals(lease.ownerBytes) ||
        observation.ownerValue?.nonce !== lease.nonce
    ) {
        throw lockError('import_lock_lost', 'Vellum no longer owns the project import lock');
    }
    const tombstone = join(paths.root, `.design-import.released-${lease.nonce}`);
    await assertSafePath(project, join(tombstone, '.probe'));
    const entries = await readdir(paths.lock);
    if (entries.length !== 1 || entries[0] !== basename(paths.owner)) {
        throw lockError('unsafe_import_lock', 'Import lock contains unexpected files during release');
    }
    try {
        await rename(paths.lock, tombstone);
        await rm(tombstone, { force: true, recursive: true });
    } catch (error) {
        throw lockError(
            'import_lock_release_failed',
            `Could not release import lock: ${error.message}`,
        );
    }
}
