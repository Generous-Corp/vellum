#include <vellum/graphics/image_asset.hpp>

#include <algorithm>
#include <cmath>
#include <iterator>
#include <set>

namespace vellum::graphics {
namespace {

bool fail(std::string* reason, std::string message) {
    if (reason != nullptr) *reason = std::move(message);
    return false;
}

bool finite(const Rect& rect) {
    return std::isfinite(rect.x) && std::isfinite(rect.y) && std::isfinite(rect.width) &&
           std::isfinite(rect.height);
}

bool check_size(std::uint32_t width, std::uint32_t height, std::size_t bytes,
                std::string* reason) {
    if (width == 0U || height == 0U) return fail(reason, "the image has no pixels");
    if (width > kMaxImageDimension || height > kMaxImageDimension) {
        return fail(reason, "the image is larger than " + std::to_string(kMaxImageDimension) +
                                " pixels on a side");
    }
    const std::uint64_t expected =
        static_cast<std::uint64_t>(width) * static_cast<std::uint64_t>(height) * 4U;
    if (expected > kMaxImageBytes) {
        return fail(reason, "the image's texels exceed " + std::to_string(kMaxImageBytes) +
                                " bytes");
    }
    if (bytes != expected) {
        return fail(reason, "the image's texel buffer does not match its dimensions");
    }
    return true;
}

bool premultiplied(std::span<const std::uint8_t> rgba) {
    for (std::size_t index = 0; index + 3U < rgba.size(); index += 4U) {
        const auto alpha = rgba[index + 3U];
        if (rgba[index] > alpha || rgba[index + 1U] > alpha || rgba[index + 2U] > alpha) {
            return false;
        }
    }
    return true;
}

}  // namespace

std::optional<ImageDimensions> png_header_dimensions(
    std::span<const std::uint8_t> encoded) noexcept {
    constexpr std::uint8_t signature[]{0x89, 'P', 'N', 'G', '\r', '\n', 0x1A, '\n'};
    constexpr std::uint8_t ihdr[]{0, 0, 0, 13, 'I', 'H', 'D', 'R'};
    // Signature, IHDR length and type, then the 4-byte width and height.
    if (encoded.size() < 24U) return std::nullopt;
    if (!std::equal(std::begin(signature), std::end(signature), encoded.begin()) ||
        !std::equal(std::begin(ihdr), std::end(ihdr), encoded.begin() + 8)) {
        return std::nullopt;
    }
    const auto big_endian = [&](std::size_t offset) {
        return (static_cast<std::uint32_t>(encoded[offset]) << 24U) |
               (static_cast<std::uint32_t>(encoded[offset + 1U]) << 16U) |
               (static_cast<std::uint32_t>(encoded[offset + 2U]) << 8U) |
               static_cast<std::uint32_t>(encoded[offset + 3U]);
    };
    return ImageDimensions{big_endian(16U), big_endian(20U)};
}

bool image_dimensions_fit(std::uint32_t width, std::uint32_t height) noexcept {
    return width > 0U && height > 0U && width <= kMaxImageDimension &&
           height <= kMaxImageDimension &&
           static_cast<std::uint64_t>(width) * static_cast<std::uint64_t>(height) * 4U <=
               kMaxImageBytes;
}

std::optional<std::string> normalize_content_hash(std::string_view value) {
    const auto space = [](char c) { return c == ' ' || c == '\t' || c == '\n' || c == '\r'; };
    while (!value.empty() && space(value.front())) value.remove_prefix(1);
    while (!value.empty() && space(value.back())) value.remove_suffix(1);
    std::string lowered(value);
    std::transform(lowered.begin(), lowered.end(), lowered.begin(), [](char c) {
        return c >= 'A' && c <= 'Z' ? static_cast<char>(c - 'A' + 'a') : c;
    });
    if (lowered.size() == 64U) lowered = "sha256:" + lowered;
    if (!is_content_hash(lowered)) return std::nullopt;
    return lowered;
}

std::shared_ptr<const ImageAsset> make_image_asset(
    std::string_view content_hash, std::uint32_t width, std::uint32_t height,
    std::span<const std::uint8_t> rgba, ImageAlpha alpha, std::string* error) {
    if (!is_content_hash(content_hash)) {
        fail(error, "the content hash is not sha256: followed by 64 lowercase hex digits");
        return nullptr;
    }
    if (!check_size(width, height, rgba.size(), error)) return nullptr;
    auto asset = std::make_shared<ImageAsset>();
    asset->content_hash = std::string(content_hash);
    asset->width = width;
    asset->height = height;
    asset->premultiplied_rgba.assign(rgba.begin(), rgba.end());
    if (alpha == ImageAlpha::straight) {
        auto& texels = asset->premultiplied_rgba;
        for (std::size_t index = 0; index < texels.size(); index += 4U) {
            const unsigned a = texels[index + 3U];
            for (std::size_t channel = 0; channel < 3U; ++channel) {
                texels[index + channel] =
                    static_cast<std::uint8_t>((texels[index + channel] * a + 127U) / 255U);
            }
        }
    } else if (!premultiplied(rgba)) {
        fail(error, "a premultiplied texel has a colour channel above its alpha");
        return nullptr;
    }
    return asset;
}

bool image_asset_is_valid(const ImageAsset& asset, std::string* reason) {
    if (!is_content_hash(asset.content_hash)) {
        return fail(reason, "the content hash is not sha256: followed by 64 lowercase hex digits");
    }
    if (!check_size(asset.width, asset.height, asset.premultiplied_rgba.size(), reason)) {
        return false;
    }
    if (!premultiplied(asset.premultiplied_rgba)) {
        return fail(reason, "a texel has a colour channel above its alpha");
    }
    return true;
}

bool image_reference_is_valid(const ImageReference& reference, std::string* reason) {
    if (!is_content_hash(reference.content_hash)) {
        return fail(reason, "the content hash is not sha256: followed by 64 lowercase hex digits");
    }
    if (reference.source_rect) {
        const auto& source = *reference.source_rect;
        if (!finite(source) || source.width < 0.0F || source.height < 0.0F) {
            return fail(reason, "the source rectangle is not finite with a non-negative size");
        }
    }
    switch (reference.fit) {
    case ImageFit::fill:
    case ImageFit::contain:
    case ImageFit::cover:
    case ImageFit::none: break;
    default: return fail(reason, "the fit is not fill, contain, cover or none");
    }
    switch (reference.sampling) {
    case ImageSampling::nearest:
    case ImageSampling::linear: break;
    default: return fail(reason, "the sampling is not nearest or linear");
    }
    return true;
}

bool scene_images_are_valid(const Scene& scene, std::string* reason) {
    std::set<std::string_view> hashes;
    for (const auto& asset : scene.images) {
        if (!asset) return fail(reason, "the scene lists a null image asset");
        std::string why;
        if (!image_asset_is_valid(*asset, &why)) {
            return fail(reason, "image asset " + asset->content_hash + " is invalid: " + why);
        }
        if (!hashes.insert(asset->content_hash).second) {
            return fail(reason, "the scene lists image asset " + asset->content_hash + " twice");
        }
    }
    return true;
}

std::shared_ptr<const ImageAsset> find_image_asset(
    const Scene& scene, std::string_view content_hash) {
    for (const auto& asset : scene.images) {
        if (asset && asset->content_hash == content_hash) return asset;
    }
    return nullptr;
}

ImageAssetIndex::ImageAssetIndex(const Scene& scene) {
    assets_.reserve(scene.images.size());
    for (const auto& asset : scene.images) {
        if (asset) assets_.emplace(asset->content_hash, &asset);
    }
}

std::shared_ptr<const ImageAsset> ImageAssetIndex::find(std::string_view content_hash) const {
    const auto found = assets_.find(content_hash);
    return found == assets_.end() ? nullptr : *found->second;
}

std::optional<ImagePlacement> image_placement(
    const ImageReference& reference, std::uint32_t width, std::uint32_t height,
    const Rect& box) {
    if (!finite(box) || box.width <= 0.0F || box.height <= 0.0F) return std::nullopt;
    const float image_width = static_cast<float>(width);
    const float image_height = static_cast<float>(height);
    Rect source = reference.source_rect.value_or(Rect{0.0F, 0.0F, image_width, image_height});
    if (!finite(source)) return std::nullopt;
    const float left = std::max(0.0F, source.x);
    const float top = std::max(0.0F, source.y);
    const float right = std::min(image_width, source.x + source.width);
    const float bottom = std::min(image_height, source.y + source.height);
    if (right <= left || bottom <= top) return std::nullopt;
    source = {left, top, right - left, bottom - top};

    float scale_x = box.width / source.width;
    float scale_y = box.height / source.height;
    switch (reference.fit) {
    case ImageFit::fill: break;
    case ImageFit::contain: scale_x = scale_y = std::min(scale_x, scale_y); break;
    case ImageFit::cover: scale_x = scale_y = std::max(scale_x, scale_y); break;
    case ImageFit::none: scale_x = scale_y = 1.0F; break;
    }
    const float drawn_width = source.width * scale_x;
    const float drawn_height = source.height * scale_y;
    const Rect drawn{box.x + (box.width - drawn_width) * 0.5F,
                     box.y + (box.height - drawn_height) * 0.5F, drawn_width, drawn_height};
    if (reference.fit == ImageFit::fill || reference.fit == ImageFit::contain) {
        return ImagePlacement{source, reference.fit == ImageFit::fill ? box : drawn};
    }
    const float crop_left = std::max(drawn.x, box.x);
    const float crop_top = std::max(drawn.y, box.y);
    const float crop_right = std::min(drawn.x + drawn.width, box.x + box.width);
    const float crop_bottom = std::min(drawn.y + drawn.height, box.y + box.height);
    if (crop_right <= crop_left || crop_bottom <= crop_top) return std::nullopt;
    return ImagePlacement{
        .source = {source.x + (crop_left - drawn.x) / scale_x,
                   source.y + (crop_top - drawn.y) / scale_y,
                   (crop_right - crop_left) / scale_x, (crop_bottom - crop_top) / scale_y},
        .destination = {crop_left, crop_top, crop_right - crop_left, crop_bottom - crop_top},
    };
}

}  // namespace vellum::graphics
