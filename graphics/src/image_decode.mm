#include <vellum/graphics/image_asset.hpp>
#include <vellum/graphics/skia_dawn_surface.hpp>

#include <CommonCrypto/CommonDigest.h>

#include "include/codec/SkCodec.h"
#include "include/codec/SkPngDecoder.h"
#include "include/core/SkData.h"
#include "include/core/SkImageInfo.h"

#include <array>
#include <cstdio>
#include <limits>
#include <optional>
#include <string>

namespace vellum::graphics {
namespace {

void set_error(std::string* error, std::string message) {
    if (error != nullptr) *error = std::move(message);
}

std::string content_hash_of(std::span<const std::uint8_t> bytes) {
    CC_SHA256_CTX context;
    CC_SHA256_Init(&context);
    constexpr std::size_t chunk = std::numeric_limits<CC_LONG>::max() / 2U;
    for (std::size_t offset = 0; offset < bytes.size(); offset += chunk) {
        const std::size_t size = std::min(chunk, bytes.size() - offset);
        CC_SHA256_Update(&context, bytes.data() + offset, static_cast<CC_LONG>(size));
    }
    std::array<unsigned char, CC_SHA256_DIGEST_LENGTH> digest{};
    CC_SHA256_Final(digest.data(), &context);
    std::string hash = "sha256:";
    char hex[3];
    for (const auto byte : digest) {
        std::snprintf(hex, sizeof hex, "%02x", byte);
        hash += hex;
    }
    return hash;
}

}  // namespace

std::shared_ptr<const ImageAsset> SkiaDawnSurface::decode_image_asset(
    std::span<const std::uint8_t> encoded, std::string_view expected_content_hash,
    std::string* error) {
    // Everything that bounds the work is checked before the bytes are hashed
    // or handed to a codec: the encoded size, then the declared dimensions.
    if (encoded.size() > kMaxEncodedImageBytes) {
        set_error(error, "the encoded image is larger than " +
                             std::to_string(kMaxEncodedImageBytes) + " bytes");
        return nullptr;
    }
    if (!SkPngDecoder::IsPng(encoded.data(), encoded.size())) {
        set_error(error, "the image is not a PNG");
        return nullptr;
    }
    const auto declared = png_header_dimensions(encoded);
    if (!declared) {
        set_error(error, "the PNG does not start with an IHDR header");
        return nullptr;
    }
    if (!image_dimensions_fit(declared->width, declared->height)) {
        set_error(error, "the PNG's size is empty or over the image budget");
        return nullptr;
    }
    std::optional<std::string> expected;
    if (!expected_content_hash.empty()) {
        expected = normalize_content_hash(expected_content_hash);
        if (!expected) {
            set_error(error, "the expected content hash is not a SHA-256 hash");
            return nullptr;
        }
    }
    const auto hash = content_hash_of(encoded);
    if (expected && *expected != hash) {
        set_error(error, "the image bytes hash to " + hash + ", not " + *expected);
        return nullptr;
    }
    auto data = SkData::MakeWithoutCopy(encoded.data(), encoded.size());
    SkCodec::Result result = SkCodec::kSuccess;
    auto codec = SkPngDecoder::Decode(data, &result);
    if (!codec || result != SkCodec::kSuccess) {
        set_error(error, "the PNG could not be read");
        return nullptr;
    }
    const auto size = codec->dimensions();
    if (size.width() <= 0 || size.height() <= 0 ||
        static_cast<std::uint32_t>(size.width()) != declared->width ||
        static_cast<std::uint32_t>(size.height()) != declared->height) {
        set_error(error, "the PNG's decoded size does not match its header");
        return nullptr;
    }
    // Texels are taken as stored: no colour management, as for every other
    // colour in a scene.
    const auto info = SkImageInfo::Make(size, kRGBA_8888_SkColorType, kPremul_SkAlphaType);
    std::vector<std::uint8_t> texels(info.computeMinByteSize());
    result = codec->getPixels(info, texels.data(), info.minRowBytes());
    if (result != SkCodec::kSuccess) {
        set_error(error, "the PNG's pixels could not be decoded");
        return nullptr;
    }
    return make_image_asset(hash, static_cast<std::uint32_t>(size.width()),
                            static_cast<std::uint32_t>(size.height()), texels,
                            ImageAlpha::premultiplied, error);
}

}  // namespace vellum::graphics
