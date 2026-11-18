import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { expectedOutputs, parseHeader } from '../../../tools/limits/generate_limits.mjs';
import * as limits from '../src/limits.generated.js';

const schema = JSON.parse(readFileSync(
    fileURLToPath(new URL('../schema/design-ir-v1.schema.json', import.meta.url)), 'utf8'));
const fixture = JSON.parse(readFileSync(
    fileURLToPath(new URL('../../../tools/limits/limits.fixture.json', import.meta.url)), 'utf8'));

test('the shared limits header matches the committed limits fixture', () => {
    const header = parseHeader(readFileSync(
        fileURLToPath(new URL('../../../graphics/include/vellum/graphics/limits.hpp', import.meta.url)), 'utf8'));
    assert.deepEqual(Object.fromEntries(header), fixture);
});

test('the generated limits modules match graphics/include/vellum/graphics/limits.hpp', () => {
    for (const [path, text] of expectedOutputs()) {
        assert.equal(readFileSync(path, 'utf8'), text, `${path} drifted; run node tools/limits/generate_limits.mjs`);
    }
});

test('the generated limits export every declaration of the header', () => {
    const header = parseHeader(readFileSync(
        fileURLToPath(new URL('../../../graphics/include/vellum/graphics/limits.hpp', import.meta.url)), 'utf8'));
    assert.deepEqual({ ...limits }, Object.fromEntries(header));
});

test('the design IR schema bounds agree with the shared limits', () => {
    const defs = schema.$defs;
    const plot = Object.values(defs).find((def) => def?.properties?.slots?.maximum !== undefined);
    assert.ok(plot, 'a schema definition bounds plot slots');
    assert.equal(plot.properties.slots.maximum, limits.MAX_PLOT_SLOTS);
    assert.equal(plot.properties.audioChannel.maximum, limits.MAX_PLOT_AUDIO_CHANNEL);
    for (const key of ['lineWidth', 'barGap', 'peakWidth']) {
        assert.equal(Math.fround(plot.properties[key].maximum), limits.MAX_PLOT_STROKE);
    }
    const node = Object.values(defs).find((def) => def?.properties?.tracks?.maxItems !== undefined);
    assert.equal(node.properties.tracks.maxItems, limits.MAX_TRACKS_PER_NODE);
    const track = Object.values(defs).find((def) => def?.properties?.keyframes?.maxItems !== undefined);
    assert.equal(track.properties.keyframes.maxItems, limits.MAX_KEYFRAMES_PER_TRACK);
    const floatBounds = [];
    const walk = (value) => {
        if (value === null || typeof value !== 'object') return;
        for (const [key, item] of Object.entries(value)) {
            if ((key === 'minimum' || key === 'maximum') && Math.abs(item) > 1e30) floatBounds.push(item);
            walk(item);
        }
    };
    walk(schema);
    assert.ok(floatBounds.length >= 8, 'the schema bounds its float fields');
    for (const bound of floatBounds) assert.equal(Math.abs(bound), limits.MAX_FLOAT32);
});
