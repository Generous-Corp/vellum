import { Stack, Text, TextInput, mount, useState } from '@vellum/ui';

function TextSemanticsProof() {
    const [value, setValue] = useState('Draft');
    const [status, setStatus] = useState('ready');
    return (
        <Stack id="proof" style={{width: 360, height: 160, padding: 16, gap: 12}}>
            <TextInput
                id="title-input"
                value={value}
                selection={{start: value.length, end: value.length}}
                accessibilityLabel="Board title"
                accessibilityValue={value}
                onChange={(event) => setValue(String(event.value))}
                onSelectionChange={() => setStatus('selected')}
                onCompositionStart={() => setStatus('composing')}
                onCompositionUpdate={() => setStatus('composing')}
                onCompositionEnd={() => setStatus('composed')}
            />
            <Text id="status" accessibilityLabel="Composition status">
                {status}
            </Text>
        </Stack>
    );
}

mount(TextSemanticsProof);
