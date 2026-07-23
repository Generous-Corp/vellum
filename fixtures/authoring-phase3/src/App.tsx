import { formatBoardTitle } from '@vellum/fixture-pure-esm-root';
import {
    Canvas,
    Design,
    Stack,
    Text,
    TextInput,
    View,
    mount,
    services,
    useEffect,
    useState,
} from '@vellum/ui';
import importedDesign from './imported-design.json';

type Item = { id: string; title: string };

function App() {
    const [title, setTitle] = useState('Roadmap');
    const [items, setItems] = useState<Item[]>([
        { id: 'one', title: 'Import design' },
        { id: 'two', title: 'Add behavior' },
    ]);
    const [status, setStatus] = useState('ready');

    useEffect(() => {
        const timer = setTimeout(() => setStatus('timer-complete'), 10);
        Promise.resolve('promise-complete').then(setStatus);
        return () => clearTimeout(timer);
    }, []);

    const addItem = () => {
        setItems((current) => [
            ...current,
            { id: `item-${current.length + 1}`, title: formatBoardTitle(title) },
        ]);
    };

    const throwMappedError = () => {
        throw new Error('phase3-source-map-proof');
    };

    return (
        <View id="phase3-app" accessibilityLabel="Phase 3 board">
            <Design
                document={importedDesign}
                actions={{ addItem }}
                bindings={{
                    schema: 'vellum.generated-bindings.v1',
                    revision: '1',
                    sourceKey: 'phase3-fixture',
                    bindings: [{
                        action: 'addItem',
                        event: 'press',
                        resolvedNodeId: 'phase3/imported-add',
                    }],
                }}
            />
            <Stack id="editor">
                <Text>{formatBoardTitle(title)}</Text>
                <TextInput
                    id="title-input"
                    value={title}
                    accessibilityLabel="Board title"
                    accessibilityValue={title}
                    selection={{ start: title.length, end: title.length }}
                    onChange={(event) => setTitle(String(event.value))}
                    onCompositionStart={() => setStatus('composing')}
                    onCompositionUpdate={() => setStatus('composing')}
                    onCompositionEnd={() => setStatus('composed')}
                />
            </Stack>
            <Stack id="item-list" scroll="vertical" accessibilityLabel="Board items">
                {items.map((item) => <Text key={item.id}>{item.title}</Text>)}
            </Stack>
            <Canvas
                id="activity-graph"
                accessibilityLabel="Activity graph"
                accessibilityValue={`${items.length} items`}
            />
            <Text id="status" accessibilityLabel="Application status">
                {status}
            </Text>
            <button id="open" onPress={() => services.files.openText()}>
                Open
            </button>
            <button id="copy" onPress={() => services.clipboard.writeText(title)}>
                Copy
            </button>
            <button id="docs" onPress={() => services.urls.openExternal('https://vellum.dev/')}>
                Documentation
            </button>
            <button id="mapped-error" onPress={throwMappedError}>
                Throw mapped error
            </button>
        </View>
    );
}

services.commands.define([{
    id: 'item.add',
    title: 'Add item',
    shortcut: 'Primary+N',
}]);

mount(App);
