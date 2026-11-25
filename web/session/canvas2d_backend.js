// A Canvas2D backend for the shared Wasm core's paint commands, bound to one
// canvas and to the size its owner measured.
//
// The backing store is sized from `surface()` on every frame, never from the
// canvas element or `devicePixelRatio`, so the canvas only changes size when
// the owner's resize path says it did.

function cssColor(red, green, blue, alpha) {
    return `rgba(${Math.round(red * 255)},${Math.round(green * 255)},` +
        `${Math.round(blue * 255)},${alpha})`;
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
        finish() {},
    });
}
