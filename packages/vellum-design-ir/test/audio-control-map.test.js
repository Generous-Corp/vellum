import test from 'node:test';
import assert from 'node:assert/strict';
import { AUDIO_CONTROL_MAP_SCHEMA, validateAudioControlMap } from '../src/index.js';

const valid = {
  $schema: AUDIO_CONTROL_MAP_SCHEMA,
  schemaVersion: 1,
  controls: [{ id: 'grain-size', role: 'knob', parameterId: 'grain-size', range: { min: 0, max: 1, step: 0.01 }, lfo: { rateMin: 0.01, rateMax: 20, depthMin: 0, depthMax: 1 } }, { id: 'freeze', role: 'toggle' }],
  audioInputs: [{ id: 'main-in', label: 'Main In', kind: 'main', channels: 2 }, { id: 'piano', label: 'Piano', kind: 'project-track', channels: 2 }],
};

test('validates stable controls and host-owned audio input kinds', () => {
  assert.deepEqual(validateAudioControlMap(valid), valid);
});

test('rejects duplicate controls and invalid bounds', () => {
  assert.throws(() => validateAudioControlMap({ ...valid, controls: [{ id: 'x', role: 'knob' }, { id: 'x', role: 'toggle' }] }), /duplicate control/);
  assert.throws(() => validateAudioControlMap({ ...valid, controls: [{ id: 'x', role: 'knob', range: { min: 1, max: 0, step: 1 } }] }), /invalid bounds/);
});

test('rejects every field the schema does not name, at every level', () => {
  const control = valid.controls[0];
  for (const [label, map] of [
    ['map', { ...valid, routes: [] }],
    ['control', { ...valid, controls: [{ ...control, dspRoute: 'daw:track1' }] }],
    ['range', { ...valid, controls: [{ ...control, range: { ...control.range, curve: 'log' } }] }],
    ['lfo', { ...valid, controls: [{ ...control, lfo: { ...control.lfo, shape: 'sine' } }] }],
    ['input', { ...valid, audioInputs: [{ ...valid.audioInputs[0], route: 'bus-1' }] }],
    ['__proto__ key', JSON.parse('{"$schema":"' + AUDIO_CONTROL_MAP_SCHEMA + '","schemaVersion":1,"controls":[{"id":"a","role":"knob","__proto__":{"x":1}}],"audioInputs":[]}')],
  ]) {
    assert.throws(() => validateAudioControlMap(map), /unknown field/, label);
  }
});

test('rejects duplicate audio input ids, non-plain objects and holes', () => {
  const input = valid.audioInputs[0];
  assert.throws(() => validateAudioControlMap({ ...valid, audioInputs: [input, { ...input, kind: 'sidechain' }] }), /duplicate audio input id/);
  assert.throws(() => validateAudioControlMap({ ...valid, controls: [Object.create({ id: 'a', role: 'knob' })] }), /plain object/);
  // eslint-disable-next-line no-sparse-arrays
  assert.throws(() => validateAudioControlMap({ ...valid, controls: [, valid.controls[1]] }), /is missing/);
});

test('rejects bad roles, kinds, channels, empty ids and non-finite numbers', () => {
  const control = valid.controls[0];
  const input = valid.audioInputs[0];
  assert.throws(() => validateAudioControlMap({ ...valid, controls: [{ id: 'a', role: 'slider' }] }), /role is unsupported/);
  assert.throws(() => validateAudioControlMap({ ...valid, controls: [{ id: '', role: 'knob' }] }), /non-empty string/);
  assert.throws(() => validateAudioControlMap({ ...valid, controls: [{ id: 'a', role: 'knob', parameterId: '' }] }), /non-empty string/);
  assert.throws(() => validateAudioControlMap({ ...valid, controls: [{ ...control, range: { min: 0, max: Infinity, step: 1 } }] }), /must be finite/);
  assert.throws(() => validateAudioControlMap({ ...valid, controls: [{ ...control, lfo: { rateMin: 1, rateMax: 1, depthMin: 0, depthMax: 1 } }] }), /invalid bounds/);
  assert.throws(() => validateAudioControlMap({ ...valid, controls: [{ ...control, lfo: { rateMin: 0, rateMax: 1, depthMin: 0, depthMax: NaN } }] }), /must be finite/);
  assert.throws(() => validateAudioControlMap({ ...valid, audioInputs: [{ ...input, kind: 'aux' }] }), /kind is unsupported/);
  assert.throws(() => validateAudioControlMap({ ...valid, audioInputs: [{ ...input, channels: 1.5 }] }), /positive integer/);
});

test('the result revalidates to itself', () => {
  const result = validateAudioControlMap(valid);
  assert.deepEqual(validateAudioControlMap(result), result);
});
