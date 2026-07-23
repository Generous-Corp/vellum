export type VellumScalar = string | number | boolean;
export type JsonValue = null | VellumScalar | JsonValue[] | { [key: string]: JsonValue };

export interface Style {
    [property: string]: VellumScalar;
}

export type EventPayload = JsonValue;
export type EventHandler = string | ((payload: EventPayload) => void);

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

export interface VellumElement<Props = ElementProps> {
    readonly type: string | symbol | Component<Props>;
    readonly props: Readonly<Props>;
}

export type Component<Props = ElementProps> = ((properties: Props) => VellumElement) & {
    displayName?: string;
    vellumId?: string;
};

export declare const Fragment: Component<{ children?: unknown }>;
export declare function jsx<Props>(
    type: Component<Props>,
    properties?: Props,
    key?: string | number,
): VellumElement;
export declare function jsx(
    type: string | symbol,
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

export interface AppOptions<Model extends JsonValue = JsonValue> {
    id?: string;
    stateVersion?: string;
    initialState?: Model;
    actions?: Record<string, (model: Model, payload: EventPayload) => Model | void>;
    render(model: Model): VellumElement;
}

declare const vellumAppBrand: unique symbol;
export interface VellumApp<Model extends JsonValue = JsonValue> {
    readonly [vellumAppBrand]: Model;
}
export declare function createApp<Model extends JsonValue = JsonValue>(
    options: AppOptions<Model> | (() => VellumElement),
): VellumApp<Model>;
export interface VellumBridge {
    readonly protocol: 'vellum.authoring-host.v1';
    renderJSON(): string;
    dispatchJSON(requestJSON: string): string;
    snapshotStateJSON(): string;
    restoreStateJSON(snapshotJSON: string): string;
}
export type WidenScalar<Value> = Value extends string ? string
    : Value extends number ? number
        : Value extends boolean ? boolean
            : Value;
export declare function mount(
    application: VellumApp | AppOptions | (() => VellumElement),
): VellumBridge;
export declare function useState<Value extends JsonValue>(
    initialValue: Value | (() => Value),
): [
    WidenScalar<Value>,
    (next: WidenScalar<Value> | ((previous: WidenScalar<Value>) => WidenScalar<Value>)) => void,
];
export declare function useMemo<Value>(
    factory: () => Value,
    dependencies: readonly unknown[],
): Value;

declare global {
    namespace JSX {
        interface Element extends VellumElement {}
        type ElementType = keyof IntrinsicElements | Component<any> | typeof Fragment;
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
