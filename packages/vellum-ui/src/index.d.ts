export type VellumScalar = string | number | boolean;
export type JsonValue = null | VellumScalar | JsonValue[] | { [key: string]: JsonValue };
export type CapabilityDeclaration =
    | 'denied'
    | 'unsupported'
    | 'v1'
    | 'user-selected-text-v1'
    | 'text-v1'
    | 'external-v1'
    | 'state-v1';
export interface ServiceCapabilities {
    commands?: 'v1' | 'denied' | 'unsupported';
    files?: 'user-selected-text-v1' | 'denied' | 'unsupported';
    clipboard?: 'text-v1' | 'denied' | 'unsupported';
    open_url?: 'external-v1' | 'denied' | 'unsupported';
    persistence?: 'state-v1' | 'denied' | 'unsupported';
}
export interface ServiceRequest {
    protocol: 'vellum.services.v1';
    kind: 'request';
    id: string;
    service: 'commands' | 'files' | 'clipboard' | 'open_url' | 'persistence';
    operation: string;
    arguments: Record<string, JsonValue>;
}
export type ServiceResponse =
    | { protocol: 'vellum.services.v1'; kind: 'response'; id: string; ok: true; value: JsonValue }
    | { protocol: 'vellum.services.v1'; kind: 'response'; id: string; ok: false;
        error: { code: string; message: string } };
export type ServiceProvider = (
    request: ServiceRequest,
) => ServiceResponse | Promise<ServiceResponse>;
export interface Services {
    commands: { execute(command: string, arguments_?: Record<string, JsonValue>): Promise<JsonValue> };
    files: { selectText(options?: Record<string, JsonValue>): Promise<JsonValue> };
    clipboard: { readText(): Promise<JsonValue>; writeText(text: string): Promise<JsonValue> };
    openUrl(url: string): Promise<JsonValue>;
    persistence: { loadState(): Promise<JsonValue>; saveState(state: JsonValue): Promise<JsonValue> };
}
export declare function createServices(
    provider: ServiceProvider,
    capabilities?: ServiceCapabilities,
): Services;
export declare const serviceCapabilities: Readonly<{
    commands: 'v1';
    files: 'user-selected-text-v1';
    clipboard: 'text-v1';
    open_url: 'external-v1';
    persistence: 'state-v1';
}>;
export declare function installServiceHost(
    provider: ServiceProvider,
    capabilities?: ServiceCapabilities,
): () => void;
export declare const services: Readonly<{
    commands: Readonly<{
        define(definitions: readonly {
            id: string;
            title: string;
            shortcut?: string;
        }[]): void;
        execute(command: string, arguments_?: Record<string, JsonValue>): Promise<JsonValue>;
        has(command: string): boolean;
        definitions(): readonly {
            id: string;
            title: string;
            shortcut?: string;
        }[];
    }>;
    files: Readonly<{
        openText(options?: Record<string, JsonValue>): Promise<JsonValue>;
        selectText(options?: Record<string, JsonValue>): Promise<JsonValue>;
    }>;
    clipboard: Readonly<{
        readText(): Promise<JsonValue>;
        writeText(text: string): Promise<JsonValue>;
    }>;
    urls: Readonly<{ openExternal(url: string): Promise<JsonValue> }>;
    persistence: Readonly<{
        loadState(): Promise<JsonValue>;
        saveState(state: JsonValue): Promise<JsonValue>;
    }>;
}>;

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
    accessibilityValue?: string;
    accessibilityRole?: 'button' | 'group' | 'image' | 'list' | 'text' | 'text-field';
    accessibilityState?: {
        disabled?: boolean;
        selected?: boolean;
        checked?: boolean | 'mixed';
        expanded?: boolean;
    };
    scroll?: 'horizontal' | 'vertical';
    onPress?: EventHandler;
    onChange?: EventHandler;
    onSubmit?: EventHandler;
    onKeyDown?: EventHandler;
    onSelectionChange?: EventHandler;
    onCompositionStart?: EventHandler;
    onCompositionUpdate?: EventHandler;
    onCompositionEnd?: EventHandler;
    children?: unknown;
    key?: string | number;
}

export interface TextInputProps extends Omit<ElementProps, 'children' | 'text' | 'source'> {
    id: string;
    value: string;
    placeholder?: string;
    /** Ordered offsets in UTF-16 code units, matching JS String and platform text APIs. */
    selection?: { start: number; end: number };
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
    hasCommand(command: string): boolean;
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
export declare function useEffect(
    effect: () => void | (() => void),
    dependencies?: readonly unknown[],
): void;

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
