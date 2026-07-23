import { Button, Stack, Text, TextInput, mount, useState } from '@vellum/ui';

function NativeBundleProof() {
    const [count, setCount] = useState(0);
    const [title, setTitle] = useState('Draft');
    return (
        <Stack id="native-proof" style={{ width: 320, height: 180 }}>
            <Text id="native-title">Native JavaScriptCore</Text>
            <Button id="native-increment" onPress={() => setCount((value) => value + 1)}>
                Count {count}
            </Button>
            <TextInput
                id="native-title-input"
                value={title}
                placeholder="Board title"
                onChange={(payload) => setTitle(payload.value)}
                onSubmit={() => setCount((value) => value + 1)}
            />
        </Stack>
    );
}

mount(NativeBundleProof);
