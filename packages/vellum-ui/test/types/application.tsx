import {
    Button,
    CustomComponent,
    Fragment,
    Stack,
    Text,
    TextRun,
    TextInput,
    createApp,
    createServices,
    materializeDesign,
    mount,
    useMemo,
    useState,
    type JsonValue,
    type VellumElement,
} from '@vellum/ui';

interface CardProps {
    id: string;
    title: string;
}

function Card({ id, title }: CardProps): VellumElement {
    const [count, setCount] = useState(0);
    const dependencies = [title] as const;
    const label = useMemo(() => `${title} ${count}`, dependencies);
    return <Button id={id} onPress={() => setCount((value) => value + 1)}>{label}</Button>;
}
Card.vellumId = 'example.card';

type Model = { screens: number; metadata: JsonValue };
const app = createApp<Model>({
    id: 'example.types',
    stateVersion: '1',
    initialState: { screens: 1, metadata: null },
    actions: {
        addScreen(model, payload) {
            return { ...model, screens: model.screens + 1, metadata: payload };
        },
    },
    render(model) {
        return (
            <Stack id="root">
                <Text id="count">{model.screens}</Text>
                <Fragment>
                    <Card key="primary" id="primary" title="Card" />
                </Fragment>
            </Stack>
        );
    },
});

const bridge = mount(app);
const rendered: string = bridge.renderJSON();
void rendered;

function EditableTitle(): VellumElement {
    const [value, setValue] = useState('Draft');
    return (
        <TextInput
            id="title-input"
            value={value}
            placeholder="Board title"
            onChange={(payload) => {
                if (payload && typeof payload === 'object' && !Array.isArray(payload) &&
                    typeof payload.value === 'string') setValue(payload.value);
            }}
            onKeyDown="handleKey"
        />
    );
}
void EditableTitle;

const custom = (
    <CustomComponent
        id="meter"
        component="level-meter"
        properties={{ values: [0.2, 0.7, 0.4] }}
        fallback={<Text id="meter-fallback">Meter unavailable</Text>}
    />
);
void custom;

const styledText = (
    <Text
        id="styled"
        style={{
            x: 12,
            y: 8,
            backgroundLinearGradient: {
                angle: 90,
                repeating: true,
                repeatLength: 32,
                stops: [
                    { position: 0, color: '#000000' },
                    { position: 1, color: '#ffffff' },
                ],
            },
            boxShadow: { blurRadius: 8, spreadRadius: 2, color: '#00000066' },
        }}
    >
        <TextRun style={{ fontWeight: 600, letterSpacing: 1 }}>Styled</TextRun>
        {/* @ts-expect-error TextRun has no independent interaction semantics. */}
        <TextRun id="link" onPress={() => {}}>Link</TextRun>
    </Text>
);
void styledText;

const imported = materializeDesign({
    root: {
        id: 'main/root',
        kind: 'view',
        children: [],
    },
}, {
    viewport: { width: 640, height: 400 },
    actions: { 'main/root': { keyDown: 'handleKey' } },
});
void imported;

const services = createServices(async (request) => ({
    protocol: 'vellum.services.v1',
    kind: 'response',
    id: request.id,
    ok: true,
    value: null,
}), {
    commands: 'v1',
    files: 'user-selected-text-v1',
    clipboard: 'text-v1',
    open_url: 'external-v1',
    persistence: 'state-v1',
});
const selectedText: Promise<JsonValue> = services.files.selectText({
    extensions: ['txt'],
});
void selectedText;
