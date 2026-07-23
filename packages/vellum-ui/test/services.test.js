import assert from 'node:assert/strict';
import test from 'node:test';
import { createServices, serviceCapabilities } from '../src/index.js';

const all = { ...serviceCapabilities };

test('services emit versioned engine-neutral requests through a fake provider', async () => {
    const requests = [];
    const services = createServices((request) => {
        requests.push(request);
        return {
            protocol: 'vellum.services.v1', kind: 'response',
            id: request.id, ok: true, value: request.operation,
        };
    }, all);
    assert.equal(await services.commands.execute('save', { force: true }), 'execute');
    assert.equal(await services.files.selectText({ extensions: ['txt'] }), 'selectText');
    assert.equal(await services.clipboard.readText(), 'readText');
    assert.equal(await services.clipboard.writeText('hello'), 'writeText');
    assert.equal(await services.openUrl('https://example.com'), 'openExternal');
    assert.equal(await services.persistence.saveState({ count: 1 }), 'saveState');
    assert.deepEqual(requests.map(({ protocol, kind, id, service }) =>
        ({ protocol, kind, id, service })), [
        { protocol: 'vellum.services.v1', kind: 'request', id: 'request-1', service: 'commands' },
        { protocol: 'vellum.services.v1', kind: 'request', id: 'request-2', service: 'files' },
        { protocol: 'vellum.services.v1', kind: 'request', id: 'request-3', service: 'clipboard' },
        { protocol: 'vellum.services.v1', kind: 'request', id: 'request-4', service: 'clipboard' },
        { protocol: 'vellum.services.v1', kind: 'request', id: 'request-5', service: 'open_url' },
        { protocol: 'vellum.services.v1', kind: 'request', id: 'request-6', service: 'persistence' },
    ]);
});

test('denied, unsupported, and undeclared capabilities never invoke providers', async () => {
    let calls = 0;
    const services = createServices(() => { calls += 1; }, {
        commands: 'denied', files: 'unsupported',
    });
    await assert.rejects(services.commands.execute('save'),
        (value) => value.code === 'capability-denied');
    await assert.rejects(services.files.selectText(),
        (value) => value.code === 'unsupported');
    await assert.rejects(services.clipboard.readText(),
        (value) => value.code === 'unsupported');
    assert.equal(calls, 0);
});

test('provider denials and malformed response envelopes fail closed', async () => {
    const denied = createServices((request) => ({
        protocol: 'vellum.services.v1', kind: 'response', id: request.id, ok: false,
        error: { code: 'capability-denied', message: 'user denied access' },
    }), all);
    await assert.rejects(denied.files.selectText(),
        (value) => value.code === 'capability-denied');
    const malformed = createServices(() => ({ ok: true }), all);
    await assert.rejects(malformed.clipboard.readText(),
        (value) => value.code === 'service-failed');
    const throwing = createServices(() => { throw new Error('host detail'); }, all);
    await assert.rejects(throwing.clipboard.readText(),
        (value) => value.code === 'service-failed' && !value.message.includes('host detail'));
});
