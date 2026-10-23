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
