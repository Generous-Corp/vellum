import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import test from 'node:test';
import { decodeAssetDataUrl, localizeAssetDataUrl } from '../src/assets.js';

test('data URL decoding is bounded and returns bytes without a path', () => {
    const decoded = decodeAssetDataUrl('data:image/svg+xml,%3Csvg%2F%3E');
    assert.equal(decoded.mimeType, 'image/svg+xml');
    assert.equal(new TextDecoder().decode(decoded.bytes), '<svg/>');
    assert.throws(() => decodeAssetDataUrl('data:image/svg+xml;base64,not-base64!'), /base64/);
    assert.throws(() => decodeAssetDataUrl('data:textplain,hello'), /MIME/);
});

test('asset localization produces a content-addressed safe relative record', async () => {
    const value = 'data:image/png;base64,iVBORw0KGgo=';
    const { asset, bytes } = await localizeAssetDataUrl(value);
    const digest = createHash('sha256').update(bytes).digest('hex');
    assert.equal(asset.contentHash, `sha256:${digest}`);
    assert.equal(asset.uri, `assets/${digest}.png`);
    assert.match(asset.id, /^data-[0-9a-f]{12}$/);
    assert.doesNotMatch(asset.uri, /(^|\/)\.\.?(\/|$)/);
});

test('asset localization rejects oversized payloads', async () => {
    await assert.rejects(
        localizeAssetDataUrl('data:text/plain,123456', { maximumBytes: 4 }),
        /oversized/,
    );
});
