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

export interface TextInputProps extends Omit<ElementProps, 'children' | 'text' | 'source'> {
    id: string;
    value: string;
    placeholder?: string;
    onChange: EventHandler;
    children?: never;
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
export declare const TextInput: Component<TextInputProps>;
export declare const Button: Component;
export declare const Image: Component;
export declare const Canvas: Component;

export interface CustomComponentProps extends ElementProps {
    component: string;
    properties?: Record<string, JsonValue>;
    fallback?: unknown;
}
export declare function CustomComponent(properties: CustomComponentProps): VellumElement;

export interface DesignNode {
    id: string;
    kind: string;
    name?: string;
    text?: string;
    properties: Record<string, JsonValue>;
    children: DesignNode[];
}
export interface DesignDocument {
    source?: { key?: string; namespace?: string; revision?: string };
    root: DesignNode;
    tokens?: Record<string, { $value?: JsonValue }>;
}
export interface DesignBinding {
    action: string;
    event: 'press' | 'change' | 'submit' | 'keyDown';
    resolvedNodeId: string;
}
export interface DesignBindingsDocument {
    schema: 'vellum.generated-bindings.v1';
    bindings: DesignBinding[];
    revision: string;
    sourceKey: string;
}
export interface DesignProps {
    document: DesignDocument;
    actions?: Record<string, EventHandler>;
    bindings?: DesignBindingsDocument | null;
}
export declare function Design(properties: DesignProps): VellumElement;

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
    readonly hostProtocol: 'vellum.authoring-host.v2';
    renderJSON(): string;
    dispatchJSON(requestJSON: string): string;
    snapshotStateJSON(): string;
    restoreStateJSON(snapshotJSON: string): string;
    pumpJSON(): string;
    isDirty(): boolean;
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

export interface MaterializedDesignNode {
    id: string;
    kind: 'view' | 'text' | 'button' | 'image' | 'canvas';
    name?: string;
    text?: string;
    properties?: Record<string, JsonValue>;
    children: MaterializedDesignNode[];
}
export interface MaterializedDesignDocument {
    root: MaterializedDesignNode;
    tokens?: Record<string, { $type?: string; $value: JsonValue }>;
}
export interface MaterializeDesignOptions {
    viewport?: { width?: number; height?: number; padding?: number };
    actions?: Record<string, Partial<Record<
        'press' | 'change' | 'submit' | 'keyDown',
        EventHandler
    >>>;
}
export declare function materializeDesign(
    document: MaterializedDesignDocument,
    options?: MaterializeDesignOptions,
): VellumElement;

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
            'text-input': TextInputProps & { primitiveVersion: 1 };
            button: ElementProps;
            image: ElementProps;
            canvas: ElementProps;
            custom: CustomComponentProps;
        }
    }
}
