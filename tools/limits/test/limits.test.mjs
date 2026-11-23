import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { copyFileSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { expectedOutput, parseHeader } from '../generate_limits.mjs';

const here = (path) => fileURLToPath(new URL(path, import.meta.url));
const generator = here('../generate_limits.mjs');
const fixtureHeader = here('../fixtures/limits_fixture.hpp');
const fixtureExpected = here('../fixtures/expected/limits_fixture.generated.js');
const label = 'tools/limits/fixtures/limits_fixture.hpp';

function runGenerator(args) {
    return spawnSync(process.execPath, [generator, ...args], { encoding: 'utf8' });
}

function withTemporaryDirectory(body) {
    const directory = mkdtempSync(join(tmpdir(), 'vellum-limits-'));
    try {
        body(directory);
    } finally {
        rmSync(directory, { recursive: true, force: true });
    }
}

test('the fixture header parses into the expected names and values', () => {
    assert.deepEqual(parseHeader(readFileSync(fixtureHeader, 'utf8')), [
        ['FIXTURE_SLOTS', 4096],
        ['FIXTURE_DEPTH', 64],
        ['FIXTURE_STROKE', 2.5],
        ['FIXTURE_HUGE', 3.4028234663852886e38],
    ]);
});

test('the committed fixture output matches what the generator renders', () => {
    assert.equal(readFileSync(fixtureExpected, 'utf8'), expectedOutput(fixtureHeader, label));
});

test('--check passes on the committed fixture output', () => {
    const result = runGenerator(['--header', fixtureHeader, '--label', label, '--out', fixtureExpected, '--check']);
    assert.equal(result.status, 0, result.stderr);
});

test('--check fails when the generated copy differs from the fixture header', () => {
    withTemporaryDirectory((directory) => {
        const output = join(directory, 'limits.generated.js');
        copyFileSync(fixtureExpected, output);
        writeFileSync(output, readFileSync(output, 'utf8').replace('= 4096;', '= 4097;'));
        const result = runGenerator(['--header', fixtureHeader, '--label', label, '--out', output, '--check']);
        assert.equal(result.status, 1);
        assert.match(result.stderr, /limits drift/);
    });
});

test('--check fails when the header changes and the generated copy does not', () => {
    withTemporaryDirectory((directory) => {
        const header = join(directory, 'limits.hpp');
        writeFileSync(header, readFileSync(fixtureHeader, 'utf8').replace('4096U', '8192U'));
        const result = runGenerator(['--header', header, '--label', label, '--out', fixtureExpected, '--check']);
        assert.equal(result.status, 1);
        assert.match(result.stderr, /limits drift/);
    });
});

test('--check fails when the generated copy is missing', () => {
    withTemporaryDirectory((directory) => {
        const result = runGenerator(['--header', fixtureHeader, '--label', label,
            '--out', join(directory, 'missing.js'), '--check']);
        assert.equal(result.status, 1);
    });
});

test('writing then checking round-trips', () => {
    withTemporaryDirectory((directory) => {
        const output = join(directory, 'limits.generated.js');
        assert.equal(runGenerator(['--header', fixtureHeader, '--label', label, '--out', output]).status, 0);
        assert.equal(readFileSync(output, 'utf8'), readFileSync(fixtureExpected, 'utf8'));
        assert.equal(runGenerator(['--header', fixtureHeader, '--label', label, '--out', output, '--check']).status, 0);
    });
});

test('the parser rejects malformed headers', () => {
    const wrap = (body) => `// vellum-limits-begin\n${body}\n// vellum-limits-end\n`;
    assert.throws(() => parseHeader('inline constexpr int kA = 1;'), /marker/);
    assert.throws(() => parseHeader(wrap('')), /no limits/);
    assert.throws(() => parseHeader(wrap('constexpr int kA = 1;')), /cannot parse/);
    assert.throws(() => parseHeader(wrap('inline constexpr std::uint32_t kA = 1U;\ninline constexpr std::uint32_t kA = 2U;')), /duplicate/);
});

test('the generator exits with a usage error when arguments are missing', () => {
    assert.equal(runGenerator([]).status, 2);
    assert.equal(runGenerator(['--header', fixtureHeader]).status, 2);
});

test('--check with several outputs fails when any one drifts and names it', () => {
    withTemporaryDirectory((directory) => {
        const drifted = join(directory, 'drifted.generated.js');
        writeFileSync(drifted, readFileSync(fixtureExpected, 'utf8').replace('= 64;', '= 65;'));
        const result = runGenerator(['--header', fixtureHeader, '--label', label,
            '--out', fixtureExpected, '--out', drifted, '--check']);
        assert.equal(result.status, 1);
        assert.match(result.stderr, /drifted\.generated\.js/);
        assert.doesNotMatch(result.stderr, /limits_fixture\.generated\.js/);
    });
});
