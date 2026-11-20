// UTF-8 byte length of a JavaScript string, matching what the native side
// measures after the string crosses the bridge. A surrogate pair is four
// bytes; a lone surrogate is encoded as U+FFFD, three bytes. Counting by hand
// keeps this independent of TextEncoder being present in the host runtime.
export function utf8ByteLength(text) {
    let bytes = 0;
    for (let index = 0; index < text.length; index += 1) {
        const unit = text.charCodeAt(index);
        if (unit < 0x80) bytes += 1;
        else if (unit < 0x800) bytes += 2;
        else if (unit >= 0xd800 && unit <= 0xdbff && index + 1 < text.length &&
                 (text.charCodeAt(index + 1) & 0xfc00) === 0xdc00) {
            bytes += 4;
            index += 1;
        } else bytes += 3;
    }
    return bytes;
}
