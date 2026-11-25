// A Canvas2D backend for the shared Wasm core's paint commands, bound to one
// canvas and to the size its owner measured.
//
// The backing store is sized from `surface()` on every frame, never from the
// canvas element or `devicePixelRatio`, so the canvas only changes size when
// the owner's resize path says it did.

import {drawVector} from '../shell/vector-canvas.js';

// The backdrop record layout, from the core's `backdrop_marks.hpp`.
const HEADER = 9;
const DOT = 0;
const GLOW = 1;
const LINE = 2;
// Where a glow's middle stop sits, as a fraction of its radius.
const GLOW_MIDDLE = 0.38;

function cssColor(red, green, blue, alpha) {
    return `rgba(${Math.round(red * 255)},${Math.round(green * 255)},` +
        `${Math.round(blue * 255)},${alpha})`;
}

// Draws a frame's backdrop marks additively, in order. `marks` is a
// Float32Array view the core owns for this call only.
export function drawBackdrop(context, marks) {
    context.save();
    context.globalCompositeOperation = 'lighter';
    let at = 0;
    while (at + HEADER <= marks.length) {
        const kind = marks[at];
        const red = marks[at + 4];
        const green = marks[at + 5];
        const blue = marks[at + 6];
        const alpha = marks[at + 7];
        if (kind === DOT) {
            context.beginPath();
            context.arc(marks[at + 1], marks[at + 2], marks[at + 3], 0, 2 * Math.PI);
            context.fillStyle = cssColor(red, green, blue, alpha);
            context.fill();
            at += HEADER;
        } else if (kind === GLOW) {
            const x = marks[at + 1];
            const y = marks[at + 2];
            const radius = marks[at + 3];
            const gradient = context.createRadialGradient(x, y, 0, x, y, radius);
            gradient.addColorStop(0, cssColor(red, green, blue, alpha));
            gradient.addColorStop(GLOW_MIDDLE, cssColor(red, green, blue, marks[at + 8]));
            gradient.addColorStop(1, cssColor(red, green, blue, 0));
            context.beginPath();
            context.arc(x, y, radius, 0, 2 * Math.PI);
            context.fillStyle = gradient;
            context.fill();
            at += HEADER;
        } else if (kind === LINE) {
            const count = marks[at + 1];
            context.beginPath();
            for (let point = 0; point < count; ++point) {
                const x = marks[at + HEADER + 2 * point];
                const y = marks[at + HEADER + 2 * point + 1];
                if (point === 0) context.moveTo(x, y);
                else context.lineTo(x, y);
            }
            context.lineWidth = marks[at + 2];
            context.strokeStyle = cssColor(red, green, blue, alpha);
            context.stroke();
            at += HEADER + 2 * count;
        } else {
            break;
        }
    }
    context.restore();
}

// `surface()` returns {cssWidth, cssHeight, scale, backingWidth, backingHeight}.
export function createCanvas2DBackend(canvas, surface) {
    const context = canvas.getContext('2d');
    return Object.freeze({
        begin(width, height, red, green, blue, alpha) {
            const size = surface();
            if (canvas.width !== size.backingWidth) canvas.width = size.backingWidth;
            if (canvas.height !== size.backingHeight) canvas.height = size.backingHeight;
            context.setTransform(
                size.backingWidth / width, 0, 0, size.backingHeight / height, 0, 0);
            context.fillStyle = cssColor(red, green, blue, alpha);
            context.fillRect(0, 0, width, height);
        },
        backdrop(marks) {
            drawBackdrop(context, marks);
        },
        rectangle(x, y, width, height, radius, red, green, blue, alpha) {
            context.beginPath();
            context.roundRect(x, y, width, height, Math.max(0, radius));
            context.fillStyle = cssColor(red, green, blue, alpha);
            context.fill();
        },
        text(text, x, baseline, size, red, green, blue, alpha) {
            context.font = `${Math.max(1, size)}px system-ui, sans-serif`;
            context.textBaseline = 'alphabetic';
            context.fillStyle = cssColor(red, green, blue, alpha);
            context.fillText(text, x, baseline);
        },
        vector(plan) {
            drawVector(context, plan);
        },
        finish() {},
    });
}
