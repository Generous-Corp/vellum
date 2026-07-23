import { Button, Stack, Text, mount, useState } from '@vellum/ui';

function NativeBundleProof() {
    const [count, setCount] = useState(0);
    return (
        <Stack id="native-proof" style={{ width: 320, height: 180 }}>
            <Text id="native-title">Native JavaScriptCore</Text>
            <Button id="native-increment" onPress={() => setCount((value) => value + 1)}>
                Count {count}
            </Button>
        </Stack>
    );
}

mount(NativeBundleProof);
