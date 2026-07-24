function ariaRole(role) {
    return {
        'text-field': 'textbox',
        text: 'text',
        image: 'img',
        list: 'list',
        button: 'button',
        group: 'group',
    }[role] || 'group';
}

function eventPayload(element, inputType) {
    return {
        value: element.value,
        inputType,
        selection: {
            start: element.selectionStart ?? element.value.length,
            end: element.selectionEnd ?? element.value.length,
        },
    };
}

export function inferredSemanticRole(node) {
    if (node.accessibilityRole) return node.accessibilityRole;
    if (node.type === 'text-input') return 'text-field';
    if (node.type === 'button') return 'button';
    if (node.type === 'text' || node.type === 'text-run') return 'text';
    if (node.type === 'image') return 'image';
    return 'group';
}

/** Owns the explicit DOM sibling used for browser text input and accessibility. */
export class BrowserSemanticAdapter {
    #dispatch;
    #elementById = new Map();
    #interaction;
    #records = [];
    #root;

    constructor(root, {dispatch, interaction}) {
        this.#root = root;
        this.#dispatch = dispatch;
        this.#interaction = interaction;
    }

    beginFrame() {
        this.#records = [];
    }

    record(value) {
        this.#records.push(value);
    }

    element(nodeId) {
        return this.#elementById.get(nodeId) || null;
    }

    #installListeners(element, record) {
        if (record.type === 'text-input') {
            element.addEventListener('input', event => {
                const live = this.#interaction(record.id, 'text-input');
                if (live.events.change) {
                    this.#dispatch(
                        live.events.change,
                        eventPayload(element, event.inputType || 'insertText'),
                    );
                }
            });
            element.addEventListener('select', () => {
                const live = this.#interaction(record.id, 'text-input');
                if (live.events.selectionChange) {
                    this.#dispatch(live.events.selectionChange, {
                        selection: {
                            start: element.selectionStart,
                            end: element.selectionEnd,
                        },
                    });
                }
            });
            for (const name of [
                'compositionstart', 'compositionupdate', 'compositionend',
            ]) {
                element.addEventListener(name, event => {
                    const live = this.#interaction(record.id, 'text-input');
                    const action = live.events[{
                        compositionstart: 'compositionStart',
                        compositionupdate: 'compositionUpdate',
                        compositionend: 'compositionEnd',
                    }[name]];
                    if (action) {
                        this.#dispatch(action, {
                            ...eventPayload(element, 'insertCompositionText'),
                            text: event.data || '',
                        });
                    }
                });
            }
            element.addEventListener('keydown', event => {
                const live = this.#interaction(record.id, 'text-input');
                if (live.events.keyDown) {
                    this.#dispatch(live.events.keyDown, {
                        key: event.key,
                        repeat: event.repeat,
                        source: 'browser',
                    });
                }
                if (event.key === 'Enter' && live.events.submit) {
                    this.#dispatch(live.events.submit, {
                        value: element.value, source: 'browser',
                    });
                }
            });
        } else if (record.type === 'button') {
            element.addEventListener('click', () => {
                const live = this.#interaction(record.id);
                if (live.events.press) {
                    this.#dispatch(
                        live.events.press, {pointerType: 'accessibility'},
                    );
                }
            });
        }
    }

    sync(width, height) {
        const liveIds = new Set();
        for (const record of this.#records) {
            liveIds.add(record.id);
            let element = this.#elementById.get(record.id);
            const expectedTag = record.type === 'text-input' ? 'INPUT' :
                record.type === 'button' ? 'BUTTON' : 'DIV';
            if (!element || element.tagName !== expectedTag) {
                if (element) element.remove();
                element = document.createElement(expectedTag.toLowerCase());
                element.className = 'vellum-semantic';
                element.dataset.vellumId = record.id;
                this.#root.append(element);
                this.#elementById.set(record.id, element);
                this.#installListeners(element, record);
            }
            element.style.left = `${record.x / width * 100}%`;
            element.style.top = `${record.y / height * 100}%`;
            element.style.width = `${record.width / width * 100}%`;
            element.style.height = `${record.height / height * 100}%`;
            element.dataset.vellumRole = record.role;
            element.setAttribute('role', ariaRole(record.role));
            element.setAttribute('aria-label', record.label);
            element.toggleAttribute('disabled', record.state.disabled === true);
            element.setAttribute(
                'aria-disabled', String(record.state.disabled === true),
            );
            for (const [state, attribute] of [
                ['selected', 'aria-selected'],
                ['checked', 'aria-checked'],
                ['expanded', 'aria-expanded'],
            ]) {
                if (record.state[state] !== undefined) {
                    element.setAttribute(attribute, String(record.state[state]));
                } else {
                    element.removeAttribute(attribute);
                }
            }
            if (record.type === 'text-input') {
                const focused = document.activeElement === element;
                const selection = focused ? {
                    start: element.selectionStart, end: element.selectionEnd,
                } : record.selection;
                if (element.value !== record.value) element.value = record.value;
                element.setAttribute('aria-valuetext', record.value);
                if (selection && selection.start <= element.value.length &&
                    selection.end <= element.value.length) {
                    element.setSelectionRange(selection.start, selection.end);
                }
            } else if (record.type === 'button') {
                element.textContent = record.label;
            } else {
                element.setAttribute('aria-valuetext', record.value);
            }
        }
        for (const [id, element] of this.#elementById) {
            if (!liveIds.has(id)) {
                element.remove();
                this.#elementById.delete(id);
            }
        }
    }
}
