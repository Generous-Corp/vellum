import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { execFile } from 'node:child_process';
import test from 'node:test';

const run = promisify(execFile);
const packageRoot = fileURLToPath(new URL('..', import.meta.url));
const cli = join(packageRoot, 'bin', 'vellum-design-ir.js');
const fixtures = fileURLToPath(new URL('../../../fixtures/design-ir/', import.meta.url));

test('CLI normalize, inspect, and reimport emit stable JSON', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'vellum-design-ir-cli-'));
    try {
        const a1 = join(directory, 'a-1.json');
        const a2 = join(directory, 'a-2.json');
        const b = join(directory, 'b.json');
        await invoke('normalize', '--input', join(fixtures, 'revision-a.source.json'), '--output', a1);
        await invoke('normalize', '--input', join(fixtures, 'revision-a.source.json'), '--output', a2);
        await invoke('normalize', '--input', join(fixtures, 'revision-b.source.json'), '--output', b);
        assert.equal(await readFile(a1, 'utf8'), await readFile(a2, 'utf8'));

        const inspect = await invoke('inspect', '--input', a1);
        assert.deepEqual(JSON.parse(inspect.stdout), {
            assets: 1,
            diagnostics: 1,
            identityStrategies: { provider: 9, structural: 1 },
            kinds: { button: 2, text: 2, view: 6 },
            losses: 0,
            nodes: 10,
            revision: 'palette-board-a',
            schemaVersion: 1,
            sourceKey: 'main',
            tokens: 5,
        });

        const reimport = await invoke(
            'reimport',
            '--previous',
            a1,
            '--next',
            b,
            '--overlay',
            join(fixtures, 'authored.overlay.json'),
        );
        const result = JSON.parse(reimport.stdout);
        assert.equal(result.accepted, true);
        assert.equal(result.resolvedBindings[0].resolvedNodeId, 'main/create-button-v2');
    } finally {
        await rm(directory, { force: true, recursive: true });
    }
});

test('CLI returns exit 2 and a reviewable report for unresolved authored work', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'vellum-design-ir-conflict-'));
    try {
        const previous = join(directory, 'previous.json');
        const next = join(directory, 'next.json');
        await invoke('normalize', '--input', join(fixtures, 'revision-a.source.json'), '--output', previous);
        await invoke('normalize', '--input', join(fixtures, 'revision-b.source.json'), '--output', next);
        await assert.rejects(
            invoke(
                'reimport',
                '--previous',
                previous,
                '--next',
                next,
                '--overlay',
                join(fixtures, 'authored-with-orphan.overlay.json'),
            ),
            (error) => {
                assert.equal(error.code, 2);
                const result = JSON.parse(error.stdout);
                assert.equal(result.accepted, false);
                assert.equal(result.report.conflicts[0].nodeId, 'main/legacy-tip');
                return true;
            },
        );
    } finally {
        await rm(directory, { force: true, recursive: true });
    }
});

function invoke(...args) {
    return run(process.execPath, [cli, ...args], {
        cwd: packageRoot,
        encoding: 'utf8',
    });
}
