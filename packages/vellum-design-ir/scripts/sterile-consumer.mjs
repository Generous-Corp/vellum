import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { basename, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

const run = promisify(execFile);
const packageRoot = fileURLToPath(new URL('..', import.meta.url));
const fixtureRoot = fileURLToPath(new URL('../../../fixtures/design-ir/', import.meta.url));
const directory = await mkdtemp(join(tmpdir(), 'vellum-design-ir-sterile-'));

try {
    const packed = await run('npm', ['pack', '--json', '--pack-destination', directory], {
        cwd: packageRoot,
        encoding: 'utf8',
    });
    const packResult = JSON.parse(packed.stdout);
    assert.equal(packResult.length, 1);
    const tarball = join(directory, packResult[0].filename);
    const consumer = join(directory, 'consumer');
    await import('node:fs/promises').then((fs) => fs.mkdir(consumer));
    await writeFile(
        join(consumer, 'package.json'),
        JSON.stringify({ name: 'sterile-vellum-design-ir-consumer', private: true, type: 'module' }),
    );
    await run('npm', ['install', '--ignore-scripts', '--offline', tarball], {
        cwd: consumer,
        encoding: 'utf8',
    });
    await writeFile(
        join(consumer, 'source.json'),
        await readFile(join(fixtureRoot, 'revision-a.source.json')),
    );
    await writeFile(
        join(consumer, 'consumer.mjs'),
        `import { readFile } from 'node:fs/promises';
import { indexTree, normalizeImport, stableStringify } from '@vellum/design-ir';
const source = JSON.parse(await readFile(new URL('./source.json', import.meta.url)));
const document = normalizeImport(source);
if (document.source.key !== 'main' || document.root.id !== 'main/app-root') process.exit(3);
process.stdout.write(stableStringify({nodes: indexTree(document.root).index.size, schemaVersion: document.schemaVersion}));
`,
    );
    const result = await run(process.execPath, ['consumer.mjs'], {
        cwd: consumer,
        encoding: 'utf8',
    });
    assert.deepEqual(JSON.parse(result.stdout), { nodes: 10, schemaVersion: 1 });
    process.stdout.write(
        JSON.stringify({
            dependencyCount: 0,
            installedTarball: basename(tarball),
            ok: true,
            sourceCheckoutRequired: false,
        }) + '\n',
    );
} finally {
    await rm(directory, { force: true, recursive: true });
}
