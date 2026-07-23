export type VellumScalar = string | number | boolean;

export interface Style {
    [property: string]: VellumScalar;
}

export interface EventPayload {
    [property: string]: unknown;
}

export type EventHandler = string | ((payload: EventPayload | null) => void);

export interface ElementProps {
    id?: string;
    style?: Style;
    text?: string;
    source?: string;
    accessibilityLabel?: string;
    onPress?: EventHandler;
    onChange?: EventHandler;
    onSubmit?: EventHandler;
    onKeyDown?: EventHandler;
    children?: unknown;
    key?: string | number;
}

export interface VellumElement {
    readonly type: string | symbol | Component;
    readonly props: Readonly<ElementProps>;
}

export type Component = (properties: ElementProps) => VellumElement;

export declare const Fragment: unique symbol;
export declare function jsx(
    type: string | symbol | Component,
    properties?: ElementProps,
    key?: string | number,
): VellumElement;
export declare const jsxs: typeof jsx;

export declare const View: Component;
export declare const Stack: Component;
export declare const Text: Component;
export declare const Button: Component;
export declare const Image: Component;
export declare const Canvas: Component;

export interface AppOptions<Model = unknown> {
    initialState?: Model;
    actions?: Record<string, (model: Model, payload: EventPayload | null) => Model | void>;
    render(model: Model): VellumElement;
}

export interface VellumApp {}
export declare function createApp<Model = unknown>(
    options: AppOptions<Model> | (() => VellumElement),
): VellumApp;
export declare function mount(
    application: VellumApp | AppOptions | (() => VellumElement),
): unknown;
export declare function useState<Value>(
    initialValue: Value | (() => Value),
): [Value, (next: Value | ((previous: Value) => Value)) => void];
export declare function useMemo<Value>(factory: () => Value, dependencies: unknown[]): Value;

declare global {
    namespace JSX {
        interface Element extends VellumElement {}
        interface ElementChildrenAttribute {
            children: {};
        }
        interface IntrinsicAttributes {
            key?: string | number;
        }
        interface IntrinsicElements {
            view: ElementProps;
            stack: ElementProps;
            text: ElementProps;
            button: ElementProps;
            image: ElementProps;
            canvas: ElementProps;
        }
    }
}
