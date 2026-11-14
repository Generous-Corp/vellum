import { Stack, Text, TextInput, TextRun, View, type Style } from '@vellum/ui';

declare const active: boolean;
declare const panel: Style;

// Optional props accept an explicit `undefined` under exactOptionalPropertyTypes.
export const conditional = (
    <Stack
        id={active ? 'on' : undefined}
        style={active ? panel : undefined}
        accessibilityLabel={active ? 'Active' : undefined}
        accessibilityState={active ? { selected: true } : undefined}
        scroll={active ? 'vertical' : undefined}
        onPress={active ? 'press' : undefined}
    >
        <Text text={active ? 'Active' : undefined} />
        <View key={active ? 1 : undefined} />
        <TextRun text={active ? 'run' : undefined} style={active ? panel : undefined} />
        <TextInput
            id="field"
            value=""
            onChange="change"
            placeholder={active ? 'Name' : undefined}
            selection={active ? { start: 0, end: 0 } : undefined}
        />
    </Stack>
);
