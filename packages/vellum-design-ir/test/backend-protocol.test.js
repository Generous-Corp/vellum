import assert from 'node:assert/strict';
import test from 'node:test';
import {
    parseArguments,
    requestedSourceKey,
    sourceArchiveArguments,
} from '../bin/backend-protocol.js';

test('backend argument parsing keeps positionals and treats only json as a flag', () => {
    assert.deepEqual(
        parseArguments([
            '--project', '/tmp/app',
            '--json',
            'source.json',
            '--source-type', 'figma',
        ]),
        {
            _: ['source.json'],
            json: true,
            project: '/tmp/app',
            'source-type': 'figma',
        },
    );
});

test('backend argument parsing rejects missing and duplicate option values', () => {
    assert.throws(
        () => parseArguments(['--project']),
        (error) => error.status === 'invalid_arguments',
    );
    assert.throws(
        () => parseArguments(['--project', 'one', '--project', 'two']),
        (error) => error.status === 'invalid_arguments',
    );
});

test('source identity arguments fail closed on ambiguity and partial archive receipts', () => {
    assert.equal(requestedSourceKey({ _: [], as: 'source-a' }), 'source-a');
    assert.throws(
        () => requestedSourceKey({ _: [], as: 'source-a', 'source-key': 'source-b' }),
        (error) => error.status === 'invalid_arguments',
    );
    assert.throws(
        () => sourceArchiveArguments({ 'source-archive': '/tmp/source.pulp.zip' }),
        (error) => error.status === 'invalid_source_archive',
    );
    assert.deepEqual(sourceArchiveArguments({
        'source-archive': '/tmp/source.pulp.zip',
        'source-archive-member': 'scene.pulp.json',
        'source-archive-name': 'source.pulp.zip',
        'source-archive-sha256': `sha256:${'0'.repeat(64)}`,
    }), {
        member: 'scene.pulp.json',
        name: 'source.pulp.zip',
        path: '/tmp/source.pulp.zip',
        sha256: `sha256:${'0'.repeat(64)}`,
    });
});
