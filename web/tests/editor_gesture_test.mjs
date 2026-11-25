// Checks the editor session's gesture lifecycle without a browser: edge order,
// one terminal event per gesture, pointer ownership and value mapping.
import assert from 'node:assert/strict';

import {DRAG_TRAVEL, GestureTracker, clampToParameter, dragValue} from '../session/gesture_tracker.js';

const mix = {id: 3, minValue: 0, maxValue: 1};
const events = [];
const tracker = new GestureTracker(event => events.push(event));

// A cancelled gesture, then a completed one.
assert.equal(tracker.begin(1, 'mix', mix, 0.5, 100), true);
assert.equal(tracker.begin(2, 'mix', mix, 0.5, 100), false, 'a second pointer cannot open a gesture');
assert.equal(tracker.move(2, 50), null, 'a pointer without the gesture cannot move it');
assert.equal(tracker.move(1, 100 - DRAG_TRAVEL / 4), 0.75);
assert.equal(tracker.move(1, 100 - DRAG_TRAVEL / 4), null, 'an unchanged value sends nothing');
assert.equal(tracker.end(2), false, 'another pointer cannot close the gesture');
assert.equal(tracker.cancel(1), true);
assert.equal(tracker.end(1), false, 'a release after a cancel emits nothing');
assert.equal(tracker.cancel(1), false, 'a lost capture after a cancel emits nothing');
assert.equal(tracker.begin(2, 'mix', mix, 0.75, 0), true);
assert.equal(tracker.move(2, DRAG_TRAVEL), 0, 'a drag clamps to the minimum');
assert.equal(tracker.end(2), true);
assert.equal(tracker.cancel(2), false, 'lostpointercapture after pointerup emits nothing');
assert.deepEqual(events.map(event => [event.type, event.value]), [
    ['begin', 0.5], ['set', 0.75], ['cancel', 0.75],
    ['begin', 0.75], ['set', 0], ['end', 0],
]);
assert.ok(events.every(event => event.param === 3 && event.target === 'mix'));

// Teardown cancels an open gesture once.
events.length = 0;
tracker.begin(4, 'mix', mix, 0.2, 0);
assert.equal(tracker.abandon(), true);
assert.equal(tracker.abandon(), false);
assert.deepEqual(events.map(event => event.type), ['begin', 'cancel']);
assert.equal(tracker.activeParam, null);

// Value mapping follows the parameter's range, step and type.
const gain = {minValue: -24, maxValue: 24, step: 0.5};
assert.equal(dragValue(gain, 0, -DRAG_TRAVEL / 2), 24);
assert.equal(clampToParameter(gain, 3.3), 3.5);
assert.equal(clampToParameter({type: 'boolean', minValue: 0, maxValue: 1}, 0.6), 1);
assert.equal(clampToParameter({}, 2), 1, 'a parameter without a range spans 0 to 1');

console.log('editor gesture lifecycle: ok');
