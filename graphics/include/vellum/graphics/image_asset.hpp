#pragma once

#include <vellum/graphics/color.hpp>
#include <vellum/graphics/scene.hpp>

#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <unordered_map>

namespace vellum::graphics {

/// The largest width or height of an `ImageAsset`, in pixels: the WebGPU
/// default `maxTextureDimension2D`, which the Dawn device is created with, so
/// every accepted asset can be uploaded as one texture.
inline constexpr std::uint32_t kMaxImageDimension = 8192U;
/// The largest texel buffer of an `ImageAsset`: 256 MiB, an 8192 by 8192
/// image.
inline constexpr std::size_t kMaxImageBytes = 256U * 1024U * 1024U;
/// The largest encoded image a decoder accepts: 64 MiB. A decoder refuses
/// larger input before hashing or parsing it, so the cost of a refusal does
/// not grow with the input.
inline constexpr std::size_t kMaxEncodedImageBytes = 64U * 1024U * 1024U;

/// The width and height a PNG declares in its header.
struct ImageDimensions final {
    std::uint32_t width = 0;
    std::uint32_t height = 0;
};

/// The dimensions in the IHDR chunk of `encoded`, which must start with the
/// PNG signature followed by a 13-byte IHDR chunk, read without decoding or
/// hashing anything else. Empty when the header is not there. The values are
/// as declared, so they still have to be checked against the image budgets.
[[nodiscard]] std::optional<ImageDimensions> png_header_dimensions(
    std::span<const std::uint8_t> encoded) noexcept;

/// Whether `width` by `height` RGBA8 texels fit `kMaxImageDimension` and
/// `kMaxImageBytes` and are not empty.
[[nodiscard]] bool image_dimensions_fit(std::uint32_t width, std::uint32_t height) noexcept;

/// What an image node draws when its asset is not in the scene: an opaque
/// mid-grey box, so a missing asset is visible rather than silently empty.
inline constexpr Color kMissingImagePlaceholder = Color::rgba(0.58F, 0.62F, 0.67F, 1.0F);

/// Whether `value` is `sha256:` followed by exactly 64 lowercase hexadecimal
/// digits, the canonical DesignIR content hash.
[[nodiscard]] constexpr bool is_content_hash(std::string_view value) noexcept {
    constexpr std::string_view prefix = "sha256:";
    if (value.size() != prefix.size() + 64U || value.substr(0, prefix.size()) != prefix) {
        return false;
    }
    for (const char digit : value.substr(prefix.size())) {
        if (!((digit >= '0' && digit <= '9') || (digit >= 'a' && digit <= 'f'))) return false;
    }
    return true;
}

/// The canonical form of a SHA-256 content hash given either bare (64
/// hexadecimal digits, the Pulp emitter's wire form) or prefixed with
/// `sha256:`, in either case, with surrounding whitespace ignored. Anything
/// else is not an integrity claim and yields `std::nullopt`.
[[nodiscard]] std::optional<std::string> normalize_content_hash(std::string_view value);

/// The alpha convention of texels handed to `make_image_asset`.
enum class ImageAlpha : std::uint8_t {
    /// Colour channels are independent of alpha, as decoded PNG data is.
    straight,
    /// Colour channels are already multiplied by alpha.
    premultiplied,
};

/// Builds an asset from RGBA8 texels, row-major, top row first, multiplying
/// straight alpha into the colour channels once, rounding to nearest. Fails
/// with a reason when the hash is not canonical, the size is zero or over
/// `kMaxImageDimension` or `kMaxImageBytes`, the buffer does not hold
/// `width * height * 4` bytes, or premultiplied texels have a colour channel
/// above their alpha.
[[nodiscard]] std::shared_ptr<const ImageAsset> make_image_asset(
    std::string_view content_hash, std::uint32_t width, std::uint32_t height,
    std::span<const std::uint8_t> rgba, ImageAlpha alpha, std::string* error = nullptr);

/// Whether `asset` could have come from `make_image_asset`.
[[nodiscard]] bool image_asset_is_valid(const ImageAsset& asset, std::string* reason = nullptr);

/// Whether `reference` names a canonical content hash and its source
/// rectangle, when present, is finite with a non-negative size.
[[nodiscard]] bool image_reference_is_valid(
    const ImageReference& reference, std::string* reason = nullptr);

/// Whether every entry of `scene.images` is non-null, passes
/// `image_asset_is_valid` (a canonical hash, a texel buffer matching its size
/// within the image budgets, and no colour channel above its alpha), and is
/// the only one with its hash. The premultiplication check reads every texel
/// once, so its cost is linear in the scene's texel bytes.
[[nodiscard]] bool scene_images_are_valid(const Scene& scene, std::string* reason = nullptr);

/// The asset `scene.images` holds under `content_hash`, or null. With a hash
/// listed twice, the first entry.
[[nodiscard]] std::shared_ptr<const ImageAsset> find_image_asset(
    const Scene& scene, std::string_view content_hash);

/// `find_image_asset` for every image of one scene, built once in time linear
/// in `scene.images`. It refers to the scene's assets and hashes, so it must
/// not outlive the scene or see `scene.images` change.
class ImageAssetIndex final {
public:
    explicit ImageAssetIndex(const Scene& scene);
    [[nodiscard]] std::shared_ptr<const ImageAsset> find(std::string_view content_hash) const;

private:
    std::unordered_map<std::string_view, const std::shared_ptr<const ImageAsset>*> assets_;
};

/// Where an image is drawn: `source` in image pixels maps onto `destination`,
/// which lies inside the node's box.
struct ImagePlacement final {
    Rect source{};
    Rect destination{};
};

/// Fits a `width` by `height` image into `box` as `reference` asks: the
/// source rectangle is clipped to the image, scaled and centred by `fit`, and
/// both rectangles are cropped to `box` for `cover` and `none`. Empty when
/// the box, the clipped source or the cropped destination has no area.
[[nodiscard]] std::optional<ImagePlacement> image_placement(
    const ImageReference& reference, std::uint32_t width, std::uint32_t height,
    const Rect& box);

}  // namespace vellum::graphics
