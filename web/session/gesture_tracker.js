// The drag and gesture lifecycle of an editor session, free of the DOM so it
// can be tested without a browser.
//
// One pointer at a time owns a gesture. A gesture opens with `begin`, sends
// one `set` per move that changes the value, and closes with exactly one
// terminal event: `end` on release or `cancel` when the browser takes the
// pointer away. A release or cancel for a pointer that owns no gesture emits
// nothing, so a `lostpointercapture` after `pointerup` cannot close a gesture
// twice.

// Vertical travel, in CSS pixels, that sweeps a control's full range.
export const DRAG_TRAVEL = 200;

export function clampToParameter(info, value) {
    const minimum = Number.isFinite(info.minValue) ? info.minValue : 0;
    const maximum = Number.isFinite(info.maxValue) ? info.maxValue : 1;
    let result = Math.min(maximum, Math.max(minimum, value));
    if (info.type === 'boolean') return result >= (minimum + maximum) / 2 ? maximum : minimum;
    if (Number.isFinite(info.step) && info.step > 0) {
        result = minimum + Math.round((result - minimum) / info.step) * info.step;
        result = Math.min(maximum, Math.max(minimum, result));
    }
    return result;
}

// The value a drag reaches after moving `deltaY` CSS pixels from its start.
// Moving up raises the value.
export function dragValue(info, startValue, deltaY) {
    const minimum = Number.isFinite(info.minValue) ? info.minValue : 0;
    const maximum = Number.isFinite(info.maxValue) ? info.maxValue : 1;
    return clampToParameter(info, startValue - (deltaY / DRAG_TRAVEL) * (maximum - minimum));
}

export class GestureTracker {
    // `emit(event)` receives {type, target, param, value} for every edge.
    constructor(emit) {
        this.emit = emit;
        this.active = null;
    }

    get activeParam() {
        return this.active ? this.active.info.id : null;
    }

    // Opens a gesture for `pointerId` on `target`. Returns false while another
    // pointer owns a gesture.
    begin(pointerId, target, info, startValue, startY) {
        if (this.active) return false;
        this.active = {pointerId, target, info, startValue, startY, value: startValue};
        this.emit({type: 'begin', target, param: info.id, value: startValue});
        return true;
    }

    // Returns the new value when the move changed it, otherwise null.
    move(pointerId, y) {
        const active = this.active;
        if (!active || active.pointerId !== pointerId) return null;
        const value = dragValue(active.info, active.startValue, y - active.startY);
        if (value === active.value) return null;
        active.value = value;
        this.emit({type: 'set', target: active.target, param: active.info.id, value});
        return value;
    }

    end(pointerId) {
        return this.#finish(pointerId, 'end');
    }

    cancel(pointerId) {
        return this.#finish(pointerId, 'cancel');
    }

    // Cancels whatever gesture is open, for teardown or a removed target.
    abandon() {
        return this.active ? this.#finish(this.active.pointerId, 'cancel') : false;
    }

    #finish(pointerId, type) {
        const active = this.active;
        if (!active || active.pointerId !== pointerId) return false;
        this.active = null;
        this.emit({type, target: active.target, param: active.info.id, value: active.value});
        return true;
    }
}
