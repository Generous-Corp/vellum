import {
    Button,
    Fragment,
    Stack,
    Text,
    createApp,
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
