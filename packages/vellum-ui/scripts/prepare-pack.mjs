import {
    chmodSync,
    copyFileSync,
    renameSync,
    rmSync,
    statSync,
} from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const binary = join(packageRoot, 'node_modules', 'esbuild', 'bin', 'esbuild');
const temporary = join(dirname(binary), `.vellum-pack-${process.pid}`);

if (process.platform !== 'win32') {
    try {
        const mode = statSync(binary).mode & 0o777;
        copyFileSync(binary, temporary);
        chmodSync(temporary, mode);
        renameSync(temporary, binary);
    } finally {
        rmSync(temporary, { force: true });
    }
}
