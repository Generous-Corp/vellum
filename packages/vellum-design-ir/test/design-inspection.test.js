import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { execFile } from 'node:child_process';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import test from 'node:test';

const run = promisify(execFile);
const packageRoot = fileURLToPath(new URL('..', import.meta.url));
const backend = join(packageRoot, 'bin', 'vellum-backend.js');
const fixtures = fileURLToPath(new URL('../../../fixtures/design-ir/', import.meta.url));

test('design check regenerates deterministically and rejects generated drift', async () => {
    const root = await mkdtemp(join(tmpdir(), 'vellum-design-check-'));
    const project = join(root, 'app');
    try {
        await importProject(project);
        const clean = await invoke(
            'design-check', '--project', project, '--json', '--as', 'main',
        );
        const cleanPayload = JSON.parse(clean.stdout);
        assert.equal(cleanPayload.status, 'design_clean');
        assert.equal(cleanPayload.data.changes.length, 0);
        assert.ok(cleanPayload.data.checkedFiles >= 10);

        const generated = join(project, 'ui', 'generated', 'main.materialized.json');
        await writeFile(generated, '{"tampered":true}\n');
        const actualBytes = await readFile(generated);

        const diff = await invoke(
            'design-diff', '--project', project, '--json', '--as', 'main',
        );
        const diffPayload = JSON.parse(diff.stdout);
        assert.equal(diffPayload.status, 'design_diff');
        assert.equal(diffPayload.data.changes.length, 1);
        const change = diffPayload.data.changes[0];
        assert.equal(change.path, 'ui/generated/main.materialized.json');
        assert.equal(change.kind, 'modified');
        assert.equal(change.actualBytes, actualBytes.length);
        assert.equal(
            change.actualSha256,
            `sha256:${createHash('sha256').update(actualBytes).digest('hex')}`,
        );
        assert.match(change.expectedSha256, /^sha256:[0-9a-f]{64}$/);

        await assert.rejects(
            invoke('design-check', '--project', project, '--json', '--as', 'main'),
            (error) => {
                assert.equal(error.code, 2);
                const payload = JSON.parse(error.stdout);
                assert.equal(payload.ok, false);
                assert.equal(payload.status, 'design_drift');
                assert.equal(payload.diagnostics[0].code, 'generated-modified');
                return true;
            },
        );
    } finally {
        await rm(root, { force: true, recursive: true });
    }
});

test('design inspection fails closed on snapshot and lock identity corruption', async () => {
    const root = await mkdtemp(join(tmpdir(), 'vellum-design-identity-'));
    const project = join(root, 'app');
    try {
        await importProject(project);
        const lockPath = join(project, 'design', 'import.lock.json');
        const lock = JSON.parse(await readFile(lockPath, 'utf8'));
        lock.sources.main.snapshotHash = `sha256:${'0'.repeat(64)}`;
        await writeFile(lockPath, `${JSON.stringify(lock)}\n`);
        await assert.rejects(
            invoke('design-diff', '--project', project, '--json'),
            (error) => {
                const payload = JSON.parse(error.stdout);
                assert.equal(payload.status, 'invalid_snapshot_provenance');
                return true;
            },
        );
    } finally {
        await rm(root, { force: true, recursive: true });
    }
});

async function importProject(project) {
    await mkdir(project, { recursive: true });
    await writeFile(
        join(project, 'framework.lock'),
        '{"schema":"vellum.project-lock.v1"}\n',
    );
    await invoke(
        'import',
        '--project', project,
        '--json',
        join(fixtures, 'revision-a.source.json'),
        '--source-type', 'figma',
        '--as', 'main',
    );
}

function invoke(...args) {
    return run(process.execPath, [backend, ...args], {
        cwd: packageRoot,
        encoding: 'utf8',
    });
}
