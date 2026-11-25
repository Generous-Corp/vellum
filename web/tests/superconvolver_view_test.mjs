// Checks the Superconvolver view's layout against values worked by hand from
// the native editor's formulas, its parameter mapping, and the slider value
// mapping, without a browser.
import assert from 'node:assert/strict';

import {trackValue} from '../session/gesture_tracker.js';
import {
    BYPASS, PARAMETERS, SLIDERS, cleanSourceName, createSuperconvolverView, sourceLabel,
    superconvolverLayout,
} from '../views/superconvolver.js';

function near(actual, expected, name) {
    for (const key of ['x', 'y', 'width', 'height']) {
        assert.ok(Math.abs(actual[key] - expected[key]) < 1e-6,
            `${name}.${key}: ${actual[key]} != ${expected[key]}`);
    }
}

const sliders = ['mix', 'size', 'gain'];

// 820 by 560, the native screenshot size: s = 1, wide.
{
    const {bounds, scale, narrow} = superconvolverLayout(820, 560, sliders);
    assert.equal(scale, 1);
    assert.equal(narrow, false);
    // avail = 560 - 64 - 20 = 476; the unpainted IR (199.92) and spectrum
    // (123.76) slots and two 12s gaps leave the controls 128.32.
    near(bounds.controls, {x: 20, y: 411.68, width: 780, height: 128.32}, 'controls');
    near(bounds.source, {x: 24, y: 44, width: 196, height: 38}, 'source');
    near(bounds['source/orb'], {x: 35, y: 56, width: 14, height: 14}, 'source/orb');
    near(bounds['source/name'], {x: 58, y: 49.48, width: 150, height: 15.6}, 'source/name');
    assert.equal(bounds.ir, undefined, 'the native editor paints no IR panel');
    assert.equal(bounds.spectrum, undefined, 'the native editor paints no spectrum panel');
    near(bounds.mix, {x: 20, y: 411.68, width: 260, height: 128.32}, 'mix');
    near(bounds.size, {x: 280, y: 411.68, width: 260, height: 128.32}, 'size');
    near(bounds.gain, {x: 540, y: 411.68, width: 260, height: 128.32}, 'gain');
    near(bounds['mix/track'], {x: 34, y: 480.9728, width: 232, height: 4}, 'mix/track');
    near(bounds['gain/track'], {x: 554, y: 480.9728, width: 232, height: 4}, 'gain/track');
    near(bounds['size/label'], {x: 294, y: 454.9728, width: 232, height: 26}, 'size/label');
    near(bounds.bypass, {x: 700, y: 18, width: 96, height: 32}, 'bypass');
    // The wordmark's tracked end: 24 + 11 (S .60 + U .73 + P .60 + E .60 + R .66
    // + C .73 + O .73 + N .73 + V .66 + O .73 + L .52 + V .66 + E .60 + R .66
    // + 14 x .24) = 24 + 11 x 12.57 = 162.27; the ring centre is 18 after it.
    near(bounds.info, {x: 166.77, y: 12, width: 27, height: 27}, 'info');
    near(bounds.tabs, {x: 269, y: 13, width: 282, height: 32}, 'tabs');
    near(bounds['tab/1'], {x: 364, y: 16, width: 92, height: 26}, 'tab/1');
    // The fade runs from the label band's top, 26 above the strip, to its bottom.
    near(bounds.fade, {x: 20, y: 385.68, width: 780, height: 154.32}, 'fade');
}

// 390 by 700: s = 1.25, narrow because 390 < 850.
{
    const {bounds, scale, narrow} = superconvolverLayout(390, 700, sliders);
    assert.equal(scale, 1.25);
    assert.equal(narrow, true);
    // top = 165, avail = 700 - 165 - 25 = 510; the unpainted slots (214.2
    // and 132.6) and two 15 gaps leave the controls 133.2.
    near(bounds.controls, {x: 25, y: 541.8, width: 340, height: 133.2}, 'controls');
    near(bounds.source, {x: 25, y: 115, width: 340, height: 45}, 'source');
    near(bounds['size/track'], {x: 25 + 340 / 3 + 17.5, y: 613.728, width: 340 / 3 - 35, height: 5},
        'size/track');
    near(bounds['mix/label'], {x: 42.5, y: 558.728, width: 340 / 3 - 35, height: 55}, 'mix/label');
    near(bounds.bypass, {x: 240, y: 22.5, width: 120, height: 40}, 'bypass');
    // Narrow tabs drop to their own row at 50s, each min(92s, 340 / 3) wide.
    near(bounds['tab/0'], {x: 195 - 170, y: 62.5, width: 340 / 3, height: 32.5}, 'narrow tab/0');
    near(bounds.fade, {x: 25, y: 486.8, width: 340, height: 188.2}, 'narrow fade');
}

// A short canvas clamps s at 0.5.
assert.equal(superconvolverLayout(400, 200, sliders).scale, 0.5);

// The strip divides by the live count: two sliders, two columns.
{
    const {bounds} = superconvolverLayout(820, 560, ['mix', 'gain']);
    near(bounds.mix, {x: 20, y: 411.68, width: 390, height: 128.32}, 'two-column mix');
    near(bounds.gain, {x: 410, y: 411.68, width: 390, height: 128.32}, 'two-column gain');
    assert.equal(bounds.size, undefined);
}

// Parameter mapping is the plug-in's ids, and only the web set exists.
assert.deepEqual(PARAMETERS, {mix: 1, size: 2, gain: 3, bypass: 4});
assert.deepEqual(SLIDERS.map(slider => [slider.id, slider.param, slider.label]),
    [['mix', 1, 'Mix'], ['size', 2, 'Size'], ['gain', 3, 'Gain']]);
assert.deepEqual([BYPASS.id, BYPASS.param, BYPASS.label], ['bypass', 4, 'Bypass']);

const web = [1, 2, 3, 4].map(id => ({id}));
const view = createSuperconvolverView(web);
const bound = view.nodes.filter(node => 'param' in node);
assert.deepEqual(bound.map(node => [node.id, node.kind, node.param]),
    [['bypass', 'toggle', 4], ['mix', 'slider', 1], ['size', 'slider', 2], ['gain', 'slider', 3]]);
assert.ok(view.nodes.every(node => !/engine|rooms|flow/i.test(node.id)));

// An adapter without Size and Bypass gets neither control.
const partial = createSuperconvolverView([{id: 1}, {id: 3}]);
assert.deepEqual(partial.nodes.filter(node => 'param' in node).map(node => node.param), [1, 3]);
const partialBounds = partial.layout(820, 560);
assert.equal(partialBounds.bypass, undefined);
near(partialBounds.gain, {x: 410, y: 411.68, width: 390, height: 128.32}, 'partial gain');

// The layout's text styles follow the mode: tracked capitals on a wide
// canvas, centered mixed case on a narrow one.
view.layout(820, 560);
assert.deepEqual(view.textStyle('mix/value'), {fontSize: 14, align: 'right'});
{
    const {advance, ...style} = view.textStyle('mix/name');
    assert.deepEqual(style, {fontSize: 9.5, align: 'left', uppercase: true, tracking: 0.26});
    assert.deepEqual(['I', 'L', 'S', 'R', 'C', 'M', ' ', '4'].map(advance),
        [0.30, 0.52, 0.60, 0.66, 0.73, 0.88, 0.40, 0.64], 'native tracked_text advances');
}
assert.deepEqual(view.textStyle('source'), {radius: 10});
view.layout(390, 700);
assert.deepEqual(view.textStyle('mix/value'), {fontSize: 13 * 1.25, align: 'center'});
assert.deepEqual(view.textStyle('mix/name'), {fontSize: 9.5 * 1.25, align: 'center'});

// The Source chip names the IR as the native clean_source_name does.
assert.equal(sourceLabel(null), 'Synthetic room');
assert.equal(sourceLabel(''), 'Synthetic room');
assert.equal(cleanSourceName('Plate.wav'), 'Plate');
assert.equal(cleanSourceName('/irs/chapel_st-vitus_48k_24bit_stereo.wav'), 'Chapel St Vitus');
assert.equal(cleanSourceName('Lexicon 480.aif'), 'Lexicon 480', 'a model number stays');
assert.equal(cleanSourceName('EMT140 plate 48000.flac'), 'EMT140 Plate');
assert.equal(cleanSourceName('48000.wav'), '48000', 'an all-engineering name falls back to the stem');
assert.equal(cleanSourceName('C:\\irs\\hall.ir.wav'), 'Hall');

// The slider value is absolute along the track, clamped at both ends.
const gain = {minValue: -24, maxValue: 24};
const track = {x: 100, y: 0, width: 200, height: 4};
assert.equal(trackValue(gain, track, 100), -24);
assert.equal(trackValue(gain, track, 200), 0);
assert.equal(trackValue(gain, track, 350), 24);
assert.equal(trackValue(gain, track, 0), -24);

// The tabs switch the field's mode, and the backdrop is the native field at
// the web build's inputs (density 1, no flow, no energy) unless the options
// replace them.
{
    const state = {time: 1 / 60, value: () => 0, impulseResponseName: null};
    const tabbed = createSuperconvolverView(web);
    tabbed.layout(820, 560);
    const tracers = tabbed.backdrop(820, 560, state);
    // 220 + 10 tracers of 30 dots, a head and a highlight, after the vignette.
    assert.equal(tracers.count, 1 + 230 * 32);
    tabbed.actions['mode-1']();
    assert.equal(tabbed.mode, 1);
    assert.equal(tabbed.backdrop(820, 560, state).count, 1 + 230, 'one streamline per tracer');
    tabbed.actions['mode-2']();
    assert.equal(tabbed.backdrop(820, 560, state).count, 1 + 229, 'one glow per fog emitter');
    const dense = createSuperconvolverView(web, {field: {density: 16}});
    assert.equal(dense.backdrop(820, 560, state).count, 1 + 380 * 32);
    assert.ok(tabbed.animated);
}

console.log('superconvolver view: ok');
