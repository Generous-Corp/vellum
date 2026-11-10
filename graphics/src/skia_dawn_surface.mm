#include <vellum/graphics/skia_dawn_surface.hpp>
#include <vellum/graphics/capture_stats.hpp>
#include <vellum/graphics/damage.hpp>
#include <vellum/graphics/dawn_bootstrap.hpp>
#include <vellum/graphics/image_asset.hpp>
#include <vellum/graphics/paint_command.hpp>
#include <vellum/graphics/render_telemetry.hpp>
#include <vellum/graphics/scene_acceptance.hpp>

#include <gpu_runtime/gpu_surface.hpp>

#include "skia_resource_caches.hpp"
#include "utf8_text.hpp"

#import <QuartzCore/CAMetalLayer.h>

#include "dawn/dawn_proc.h"
#include "dawn/native/DawnNative.h"
#include "include/core/SkCanvas.h"
#include "include/core/SkBlurTypes.h"
#include "include/core/SkColorSpace.h"
#include "include/core/SkData.h"
#include "include/core/SkFontMgr.h"
#include "include/core/SkMaskFilter.h"
#include "include/core/SkMatrix.h"
#include "include/core/SkTypeface.h"
#include "include/core/SkImageInfo.h"
#include "include/core/SkImage.h"
#include "include/core/SkPaint.h"
#include "include/core/SkPath.h"
#include "include/core/SkPathBuilder.h"
#include "include/core/SkPathEffect.h"
#include "include/core/SkSamplingOptions.h"
#include "include/core/SkPixmap.h"
#include "include/core/SkRRect.h"
#include "include/core/SkStream.h"
#include "include/core/SkSurface.h"
#include "include/core/SkVertices.h"
#include "include/encode/SkPngEncoder.h"
#include "include/effects/SkDashPathEffect.h"
#include "include/effects/SkGradient.h"
#include "include/gpu/graphite/BackendTexture.h"
#include "include/gpu/graphite/Context.h"
#include "include/gpu/graphite/ContextOptions.h"
#include "include/gpu/graphite/Image.h"
#include "include/gpu/graphite/Recorder.h"
#include "include/gpu/graphite/Recording.h"
#include "include/gpu/graphite/Surface.h"
#include "include/gpu/graphite/dawn/DawnBackendContext.h"
#include "include/gpu/graphite/dawn/DawnGraphiteTypes.h"
#include "include/ports/SkFontMgr_mac_ct.h"
#include "modules/skparagraph/include/FontCollection.h"
#include "modules/skparagraph/include/Paragraph.h"
#include "modules/skparagraph/include/ParagraphBuilder.h"
#include "modules/skparagraph/include/ParagraphStyle.h"
#include "modules/skparagraph/include/TextStyle.h"
#include "modules/skparagraph/include/TypefaceFontProvider.h"
#include "modules/skunicode/include/SkUnicode_icu.h"
#include "webgpu/webgpu_cpp.h"

#include <dlfcn.h>

#include <algorithm>
#include <functional>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <limits>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <unordered_map>
#include <utility>
#include <vector>

namespace vellum::graphics {
namespace {

void set_error(std::string* destination, std::string message) {
    if (destination != nullptr) {
        *destination = std::move(message);
    }
}

std::string backend_name(wgpu::BackendType backend) {
    switch (backend) {
        case wgpu::BackendType::Metal: return "Metal";
        case wgpu::BackendType::D3D12: return "D3D12";
        case wgpu::BackendType::Vulkan: return "Vulkan";
        case wgpu::BackendType::OpenGL: return "OpenGL";
        case wgpu::BackendType::OpenGLES: return "OpenGLES";
        case wgpu::BackendType::Null: return "Null";
        case wgpu::BackendType::WebGPU: return "WebGPU";
        default: return "Unknown";
    }
}

std::string string_view(wgpu::StringView value) {
    if (value.data == nullptr || value.length == 0) {
        return {};
    }
    return {value.data, value.length};
}

SkColor4f sk_color(Color color) {
    return {color.red, color.green, color.blue, color.alpha};
}

SkColor4f sk_color(Color color, float opacity) {
    return {color.red, color.green, color.blue, color.alpha * opacity};
}

SkMatrix sk_matrix(const Affine2D& transform) {
    return SkMatrix::MakeAll(
        transform.a, transform.c, transform.tx,
        transform.b, transform.d, transform.ty,
        0.0F, 0.0F, 1.0F);
}

SkRRect rounded_rect(const SkRect& rect, const CornerRadii& radii, float outset = 0.0F) {
    const auto grown = [outset](float radius) {
        const float value = std::max(0.0F, radius + outset);
        return SkVector{value, value};
    };
    const SkVector corners[4]{
        grown(radii.top_left),
        grown(radii.top_right),
        grown(radii.bottom_right),
        grown(radii.bottom_left),
    };
    SkRRect result;
    result.setRectRadii(rect, corners);
    return result;
}

std::filesystem::path font_directory(std::string_view requested) {
    if (!requested.empty()) return std::filesystem::path(requested);
    Dl_info info{};
    if (dladdr(reinterpret_cast<const void*>(&font_directory), &info) != 0 &&
        info.dli_fname != nullptr) {
        const auto library_directory = std::filesystem::path(info.dli_fname).parent_path();
        const auto bundled = library_directory.parent_path() /
            "Resources" / "vellum" / "fonts";
        if (std::filesystem::is_directory(bundled)) return bundled;
        const auto installed = library_directory /
            VELLUM_DATADIR_FROM_LIBDIR / "vellum" / "fonts";
        if (std::filesystem::is_directory(installed)) return installed;
        const auto build_tree = library_directory /
            VELLUM_BUILD_DATADIR_FROM_PROVIDER_DIR / "vellum" / "fonts";
        if (std::filesystem::is_directory(build_tree)) return build_tree;
    }
    return {};
}

SkFontStyle font_style(const TextStyle& style) {
    return {std::clamp(style.font_weight, 100, 900),
            SkFontStyle::kNormal_Width, SkFontStyle::kUpright_Slant};
}

struct PackagedFontSet {
    sk_sp<skia::textlayout::FontCollection> collection;
    std::vector<sk_sp<SkTypeface>> typefaces;
};

bool is_nonrendering_code_point(std::uint32_t code_point) {
    return code_point <= 0x1FU || (code_point >= 0x7FU && code_point <= 0x9FU) ||
        (code_point >= 0x200BU && code_point <= 0x200FU) || code_point == 0x2060U ||
        (code_point >= 0x202AU && code_point <= 0x202EU) ||
        (code_point >= 0x2066U && code_point <= 0x2069U) ||
        (code_point >= 0xFE00U && code_point <= 0xFE0FU) ||
        (code_point >= 0xE0100U && code_point <= 0xE01EFU);
}

bool has_packaged_glyph(
    std::uint32_t code_point,
    const std::vector<sk_sp<SkTypeface>>& typefaces) {
    if (is_nonrendering_code_point(code_point)) return true;
    return std::any_of(typefaces.begin(), typefaces.end(), [code_point](const auto& face) {
        return face->unicharToGlyph(static_cast<SkUnichar>(code_point)) != 0U;
    });
}

std::string replace_unsupported_text(
    std::string_view text,
    const std::vector<sk_sp<SkTypeface>>& typefaces) {
    return detail::replace_unsupported_text(text, [&typefaces](std::uint32_t code_point) {
        return has_packaged_glyph(code_point, typefaces);
    });
}

// Textures uploaded for one frame, so a texture shared by several meshes (a
// glyph atlas, say) is hashed and looked up once.
struct MeshTextureCache final {
    skgpu::graphite::Recorder* recorder = nullptr;
    std::unordered_map<const MeshTexture*, sk_sp<SkImage>> images;
    /// The same for image assets, which the scene keeps alive for the frame.
    std::unordered_map<const ImageAsset*, sk_sp<SkImage>> assets;
};

// What one frame draws with: the per-frame texture map, the surface's
// cross-frame caches, and the count of offscreen layers the frame opens.
struct FrameResources final {
    MeshTextureCache meshes;
    detail::SkiaResourceCaches* caches = nullptr;
    std::size_t layers = 0;
    /// Textures this frame cached. Their uploads exist only in this frame's
    /// recording, so a frame that fails before it is submitted drops them.
    std::vector<detail::TextureKey> uploaded;
};

sk_sp<SkShader> linear_gradient_shader(
    const ResolvedLinearGradient& gradient, float opacity, FrameResources& resources,
    std::string* error) {
    if (gradient.stops.size() < 2U) {
        set_error(error, "Vellum linear gradient needs at least two stops");
        return nullptr;
    }
    const float vector_x = gradient.end_x - gradient.start_x;
    const float vector_y = gradient.end_y - gradient.start_y;
    const float length = std::hypot(vector_x, vector_y);
    if (length <= 0.0F) {
        set_error(error, "Vellum linear gradient has a zero-length paint box");
        return nullptr;
    }
    const float direction_x = vector_x / length;
    const float direction_y = vector_y / length;
    const float cycle = gradient.repeating && gradient.repeat_length > 0.0F
        ? gradient.repeat_length : length;
    const SkPoint points[]{
        {gradient.start_x, gradient.start_y},
        {gradient.start_x + direction_x * cycle,
         gradient.start_y + direction_y * cycle},
    };
    detail::GradientKey key{
        .points = {points[0].fX, points[0].fY, points[1].fX, points[1].fY},
        .repeating = gradient.repeating,
    };
    key.colors.reserve(gradient.stops.size());
    key.positions.reserve(gradient.stops.size());
    for (const auto& stop : gradient.stops) {
        const auto color = sk_color(stop.color, opacity);
        key.colors.push_back({color.fR, color.fG, color.fB, color.fA});
        key.positions.push_back(stop.position);
    }
    sk_sp<SkShader> shader;
    auto* caches = resources.caches;
    if (const auto* cached = caches != nullptr ? caches->gradients.find(key, caches->frame)
                                               : nullptr) {
        shader = *cached;
    } else {
        std::vector<SkColor4f> colors;
        colors.reserve(key.colors.size());
        for (const auto& color : key.colors) {
            colors.push_back({color.red, color.green, color.blue, color.alpha});
        }
        const SkGradient shader_gradient{
            SkGradient::Colors(
                colors, key.positions,
                gradient.repeating ? SkTileMode::kRepeat : SkTileMode::kClamp),
            {},
        };
        shader = SkShaders::LinearGradient(points, shader_gradient);
        if (!shader) {
            set_error(error, "Vellum could not create the linear-gradient shader");
            return nullptr;
        }
        if (caches != nullptr) {
            const auto bytes = detail::gradient_bytes(key);
            caches->gradients.insert(std::move(key), shader, bytes, caches->frame);
        }
    }
    return shader;
}

bool draw_linear_gradient(
    SkCanvas& canvas, const PaintCommand& command,
    const ResolvedLinearGradient& gradient, float opacity, FrameResources& resources,
    std::string* error) {
    if (command.bounds.width <= 0.0F || command.bounds.height <= 0.0F) return true;
    auto shader = linear_gradient_shader(gradient, opacity, resources, error);
    if (!shader) return false;

    canvas.save();
    const auto rect = SkRect::MakeXYWH(
        command.bounds.x, command.bounds.y,
        command.bounds.width, command.bounds.height);
    if (is_rounded(command.corner_radii)) {
        canvas.clipRRect(rounded_rect(rect, command.corner_radii), true);
    } else {
        canvas.clipRect(rect, true);
    }
    SkPaint paint;
    paint.setAntiAlias(true);
    paint.setShader(std::move(shader));
    canvas.drawRect(rect, paint);
    canvas.restore();
    return true;
}

bool build_text_paragraph(
    const std::vector<TextRun>& requested_runs,
    std::string_view requested_directory,
    std::unique_ptr<skia::textlayout::Paragraph>& paragraph,
    TextMetrics& metrics,
    std::string* error) {
    static std::mutex paragraph_mutex;
    const std::scoped_lock lock(paragraph_mutex);
    static const sk_sp<SkFontMgr> manager = SkFontMgr_New_CoreText(nullptr);
    static const sk_sp<SkUnicode> unicode = SkUnicodes::ICU::Make();
    static std::unordered_map<std::string, PackagedFontSet> font_cache;
    const auto directory = font_directory(requested_directory);
    if (!manager || !unicode || directory.empty() ||
        !std::filesystem::is_directory(directory)) {
        set_error(error, "Vellum packaged font directory is unavailable: " + directory.string());
        return false;
    }

    const auto cache_key = directory.lexically_normal().string();
    auto cached = font_cache.find(cache_key);
    if (cached == font_cache.end()) {
        PackagedFontSet loaded;
        auto provider = sk_make_sp<skia::textlayout::TypefaceFontProvider>();
        constexpr std::array<std::string_view, 7> packaged_fonts{
            "Inter-Regular.ttf", "Jost-Regular.ttf", "Jost-Medium.ttf",
            "Jost-SemiBold.ttf", "Jost-Bold.ttf", "NotoSansJP[wght].ttf",
            "NotoSansArabic[wdth,wght].ttf",
        };
        for (const auto filename : packaged_fonts) {
            const auto path = directory / filename;
            auto typeface = manager->makeFromFile(path.c_str());
            if (!typeface) {
                set_error(error, "Vellum packaged font is unavailable: " + path.string());
                return false;
            }
            loaded.typefaces.push_back(typeface);
            provider->registerTypeface(std::move(typeface));
        }
        if (!has_packaged_glyph(static_cast<std::uint32_t>('?'), loaded.typefaces)) {
            set_error(error, "Vellum packaged fonts have no unsupported-text placeholder");
            return false;
        }
        loaded.collection = sk_make_sp<skia::textlayout::FontCollection>();
        loaded.collection->setAssetFontManager(provider);
        loaded.collection->setDefaultFontManager(provider, std::vector<SkString>{
            SkString("Inter"), SkString("Noto Sans JP"), SkString("Noto Sans Arabic")});
        loaded.collection->disableFontFallback();
        cached = font_cache.emplace(cache_key, std::move(loaded)).first;
    }

    skia::textlayout::ParagraphStyle paragraph_style;
    paragraph_style.setMaxLines(1U);
    paragraph_style.setTextAlign(skia::textlayout::TextAlign::kLeft);
    auto builder = skia::textlayout::ParagraphBuilder::make(
        paragraph_style, cached->second.collection, unicode);
    if (!builder) {
        set_error(error, "Vellum could not create the attributed-text paragraph builder");
        return false;
    }

    for (const auto& requested : requested_runs) {
        if (requested.text.empty()) continue;
        skia::textlayout::TextStyle style;
        style.setFontFamilies({
            SkString(requested.style.font_family.empty()
                ? "Inter" : requested.style.font_family.c_str()),
            SkString("Noto Sans Arabic"),
            SkString("Noto Sans JP"),
            SkString("Inter"),
        });
        style.setFontStyle(font_style(requested.style));
        style.setFontSize(std::max(1.0F, requested.style.font_size));
        style.setLetterSpacing(requested.style.letter_spacing);
        style.setFontEdging(SkFont::Edging::kAntiAlias);
        SkPaint foreground;
        foreground.setAntiAlias(true);
        foreground.setColor4f(sk_color(requested.style.color));
        style.setForegroundPaint(foreground);
        int decoration = skia::textlayout::kNoDecoration;
        if (requested.style.underline) decoration |= skia::textlayout::kUnderline;
        if (requested.style.strikethrough) decoration |= skia::textlayout::kLineThrough;
        style.setDecoration(static_cast<skia::textlayout::TextDecoration>(decoration));
        style.setDecorationColor(foreground.getColor());
        builder->pushStyle(style);
        const auto text = replace_unsupported_text(
            requested.text, cached->second.typefaces);
        builder->addText(text.data(), text.size());
        builder->pop();
    }

    paragraph = builder->Build();
    if (!paragraph) {
        set_error(error, "Vellum could not build the attributed-text paragraph");
        return false;
    }
    paragraph->layout(1000000000.0F);
    metrics = {
        .width = paragraph->getMaxIntrinsicWidth(),
        .ascent = paragraph->getAlphabeticBaseline(),
        .descent = std::max(
            0.0F, paragraph->getHeight() - paragraph->getAlphabeticBaseline()),
        .baseline = paragraph->getAlphabeticBaseline(),
    };
    return true;
}

bool draw_rectangle(SkCanvas& canvas, const PaintCommand& command, float opacity,
                    FrameResources& resources, std::string* error) {
    const auto rect = SkRect::MakeXYWH(
        command.bounds.x, command.bounds.y,
        command.bounds.width, command.bounds.height);
    for (const auto& shadow : command.box_shadows) {
        SkPaint shadow_paint;
        shadow_paint.setAntiAlias(true);
        shadow_paint.setColor4f(sk_color(shadow.color, opacity));
        if (shadow.blur_radius > 0.0F) {
            shadow_paint.setMaskFilter(SkMaskFilter::MakeBlur(
                kNormal_SkBlurStyle, shadow.blur_radius * 0.5F, true));
        }
        const auto shadow_rect = SkRect::MakeXYWH(
            command.bounds.x + shadow.offset_x - shadow.spread_radius,
            command.bounds.y + shadow.offset_y - shadow.spread_radius,
            command.bounds.width + shadow.spread_radius * 2.0F,
            command.bounds.height + shadow.spread_radius * 2.0F);
        canvas.save();
        if (is_rounded(command.corner_radii)) {
            canvas.clipRRect(
                rounded_rect(rect, command.corner_radii), SkClipOp::kDifference, true);
        } else {
            canvas.clipRect(rect, SkClipOp::kDifference, true);
        }
        canvas.drawRRect(
            rounded_rect(shadow_rect, command.corner_radii, shadow.spread_radius),
            shadow_paint);
        canvas.restore();
    }

    SkPaint paint;
    paint.setAntiAlias(true);
    paint.setColor4f(sk_color(command.fill, opacity));
    if (is_rounded(command.corner_radii)) {
        canvas.drawRRect(rounded_rect(rect, command.corner_radii), paint);
    } else {
        canvas.drawRect(rect, paint);
    }
    for (const auto& declared : command.fill_gradients) {
        const auto gradient = resolve_linear_gradient(command.bounds, declared);
        if (!draw_linear_gradient(canvas, command, gradient, opacity, resources, error)) {
            return false;
        }
    }
    return true;
}

SkRect layer_rect(const Rect& rect) {
    return SkRect::MakeXYWH(rect.x, rect.y, rect.width, rect.height).makeOutset(1.0F, 1.0F);
}

using ParagraphSlot = detail::CachedParagraph;

// Finds the laid-out paragraph for `runs`, or builds and caches it.
bool paragraph_for(std::vector<TextRun> runs, std::string_view directory,
                   FrameResources& resources, ParagraphSlot& slot, std::string* error) {
    auto* caches = resources.caches;
    detail::ParagraphKey key{.font_directory = std::string(directory), .runs = std::move(runs)};
    if (caches != nullptr) {
        if (const auto* cached = caches->paragraphs.find(key, caches->frame)) {
            slot = *cached;
            return true;
        }
    }
    std::unique_ptr<skia::textlayout::Paragraph> built;
    if (!build_text_paragraph(key.runs, directory, built, slot.metrics, error)) return false;
    slot.paragraph = std::move(built);
    if (caches != nullptr) {
        const auto bytes = detail::estimated_paragraph_bytes(key);
        caches->paragraphs.insert(std::move(key), slot, bytes, caches->frame);
    }
    return true;
}

bool draw_text(SkCanvas& canvas, const PaintCommand& command, float opacity,
               float layer_opacity, std::string_view directory, FrameResources& resources,
               ParagraphSlot* slot, std::string* error) {
    std::vector<TextRun> runs = command.text_runs;
    if (runs.empty() && !command.text.empty()) {
        runs.push_back({
            .text = command.text,
            .style = {
                .font_family = command.font_family,
                .font_weight = command.font_weight,
                .font_size = command.font_size,
                .letter_spacing = command.letter_spacing,
                .color = command.fill,
                .underline = command.underline,
                .strikethrough = command.strikethrough,
            },
        });
    }
    for (auto& run : runs) run.style.color.alpha *= opacity;
    ParagraphSlot built;
    ParagraphSlot& cached = slot != nullptr ? *slot : built;
    if (!cached.paragraph &&
        !paragraph_for(std::move(runs), directory, resources, cached, error)) {
        return false;
    }
    auto& paragraph = cached.paragraph;
    const auto& metrics = cached.metrics;
    if (layer_opacity < 1.0F) {
        const float overhang = command.font_size;
        const auto extent = SkRect::MakeXYWH(
            command.bounds.x - overhang, command.bounds.y - overhang,
            std::max(command.bounds.width, metrics.width) + overhang * 2.0F,
            std::max(command.bounds.height, metrics.ascent + metrics.descent) +
                overhang * 2.0F);
        canvas.saveLayerAlphaf(&extent, layer_opacity);
        ++resources.layers;
    }
    paragraph->paint(&canvas, command.bounds.x, command.bounds.y);
    if (layer_opacity < 1.0F) canvas.restore();
    return true;
}

// Uploads premultiplied RGBA8 texels, or reuses the cross-frame upload of the
// same size and texel digest. `digest` is called only when the texture cache
// is on. `what` names the texture in errors.
sk_sp<SkImage> upload_texels(
    std::uint32_t width, std::uint32_t height, std::span<const std::uint8_t> texels,
    const std::function<detail::TextureDigest()>& digest, FrameResources& resources,
    const char* what, std::string* error) {
    // Across frames a texture is found by its content, never its address: a
    // producer may reuse an allocation for different texels.
    auto* caches = resources.caches;
    detail::TextureKey key{.width = width, .height = height};
    if (caches != nullptr && !caches->textures.enabled()) caches = nullptr;
    if (caches != nullptr) {
        key.digest = digest();
        if (const auto* cached = caches->textures.find(key, caches->frame)) return *cached;
    }
    const auto info = SkImageInfo::Make(
        static_cast<int>(width), static_cast<int>(height),
        kRGBA_8888_SkColorType, kPremul_SkAlphaType);
    auto raster = SkImages::RasterFromData(
        info, SkData::MakeWithCopy(texels.data(), texels.size()),
        static_cast<std::size_t>(width) * 4U);
    if (!raster) {
        set_error(error, std::string("Skia could not wrap ") + what);
        return nullptr;
    }
    auto* recorder = resources.meshes.recorder;
    auto uploaded = recorder != nullptr
        ? SkImages::TextureFromImage(recorder, raster.get(), {})
        : nullptr;
    if (!uploaded) {
        set_error(error, std::string("Skia Graphite could not upload ") + what);
        return nullptr;
    }
    if (caches != nullptr &&
        caches->textures.insert(key, uploaded, texels.size(), caches->frame)) {
        resources.uploaded.push_back(key);
    }
    return uploaded;
}

sk_sp<SkImage> mesh_texture_image(
    const MeshTexture& texture, FrameResources& resources, std::string* error) {
    auto& cache = resources.meshes;
    if (const auto found = cache.images.find(&texture); found != cache.images.end()) {
        return found->second;
    }
    auto image = upload_texels(
        texture.width, texture.height, texture.premultiplied_rgba,
        [&texture] { return detail::sha256(texture.premultiplied_rgba); }, resources,
        "a mesh texture", error);
    if (image) cache.images.emplace(&texture, image);
    return image;
}

// An image asset's texture. Its texel digest is remembered under the content
// hash while the hash still names this asset (see `detail::ImageDigest`).
sk_sp<SkImage> image_asset_texture(
    const std::shared_ptr<const ImageAsset>& asset, FrameResources& resources,
    std::string* error) {
    auto& cache = resources.meshes;
    if (const auto found = cache.assets.find(asset.get()); found != cache.assets.end()) {
        return found->second;
    }
    const auto digest = [&asset, &resources] {
        auto* caches = resources.caches;
        if (auto* known = caches->image_digests.find(asset->content_hash, caches->frame);
            known != nullptr && known->asset.lock() == asset) {
            return known->digest;
        }
        const auto computed = detail::sha256(asset->premultiplied_rgba);
        caches->image_digests.insert(asset->content_hash, {asset, computed},
                                     detail::kImageDigestBytes, caches->frame);
        return computed;
    };
    auto image = upload_texels(asset->width, asset->height, asset->premultiplied_rgba, digest,
                               resources, "an image asset", error);
    if (image) cache.assets.emplace(asset.get(), image);
    return image;
}

// Vertex colours arrive premultiplied; SkVertices takes straight-alpha SkColor.
SkColor straight_vertex_color(std::uint32_t premultiplied_rgba) {
    const auto channel = [premultiplied_rgba](int shift) {
        return (premultiplied_rgba >> shift) & 0xFFU;
    };
    const std::uint32_t alpha = channel(0);
    const auto unpremultiply = [alpha](std::uint32_t value) {
        if (alpha == 0U) return 0U;
        return std::min(255U, (value * 255U + alpha / 2U) / alpha);
    };
    return SkColorSetARGB(alpha, unpremultiply(channel(24)),
                          unpremultiply(channel(16)), unpremultiply(channel(8)));
}

bool draw_mesh(SkCanvas& canvas, const PaintCommand& command,
               FrameResources& resources, std::string* error) {
    if (!command.mesh) return true;
    const auto& mesh = *command.mesh;
    std::string reason;
    if (!mesh_is_valid(mesh, &reason)) {
        set_error(error, "mesh " + command.node_id + " cannot be drawn: " + reason);
        return false;
    }
    if (mesh.indices.empty()) return true;
    if (mesh.indices.size() > static_cast<std::size_t>(std::numeric_limits<int>::max())) {
        set_error(error, "mesh " + command.node_id + " has too many indices");
        return false;
    }

    sk_sp<SkImage> image;
    if (mesh.texture) {
        image = mesh_texture_image(*mesh.texture, resources, error);
        if (!image) return false;
    }
    // Expand the indexed list: SkVertices indices are 16-bit, and a mesh may
    // name more vertices than that.
    const auto count = mesh.indices.size();
    std::vector<SkPoint> positions(count);
    std::vector<SkColor> colors(count);
    std::vector<SkPoint> texture_coordinates(image ? count : 0U);
    for (std::size_t index = 0; index < count; ++index) {
        const auto& vertex = mesh.vertices[mesh.indices[index]];
        positions[index] = {command.bounds.x + vertex.x, command.bounds.y + vertex.y};
        colors[index] = straight_vertex_color(vertex.premultiplied_rgba);
        if (image) {
            texture_coordinates[index] = {
                vertex.u * static_cast<float>(mesh.texture->width),
                vertex.v * static_cast<float>(mesh.texture->height)};
        }
    }
    const auto vertices = SkVertices::MakeCopy(
        SkVertices::kTriangles_VertexMode, static_cast<int>(count), positions.data(),
        image ? texture_coordinates.data() : nullptr, colors.data());
    if (!vertices) {
        set_error(error, "Skia could not build the vertices of mesh " + command.node_id);
        return false;
    }

    SkPaint paint;
    paint.setAntiAlias(false);
    // drawVertices combines the paint's colour (or shader) with the vertex
    // colours through the blend mode, and the default paint colour is opaque
    // black. White under kModulate leaves the vertex colours as they are.
    paint.setColor(SK_ColorWHITE);
    if (image) {
        // Texture coordinates are in texel units, so a coordinate inside a
        // texel samples that texel.
        paint.setShader(image->makeShader(
            SkTileMode::kClamp, SkTileMode::kClamp,
            SkSamplingOptions(SkFilterMode::kNearest)));
    }
    canvas.save();
    if (mesh.clip_enabled) {
        canvas.clipRect(
            SkRect::MakeXYWH(command.bounds.x + mesh.clip.x, command.bounds.y + mesh.clip.y,
                             std::max(0.0F, mesh.clip.width),
                             std::max(0.0F, mesh.clip.height)),
            false);
    }
    // With a texture, the shader's texel is multiplied by the vertex colour.
    canvas.drawVertices(vertices, SkBlendMode::kModulate, paint);
    canvas.restore();
    return true;
}

SkPath sk_path(const PaintCommand& command) {
    const auto& path = *command.path;
    SkPathBuilder builder(path.fill_rule == FillRule::evenodd ? SkPathFillType::kEvenOdd
                                                              : SkPathFillType::kWinding);
    const float x = command.bounds.x;
    const float y = command.bounds.y;
    std::size_t offset = 0;
    for (const auto verb : path.verbs) {
        const float* v = path.values.data() + offset;
        offset += path_verb_arity(verb);
        switch (verb) {
        case PathVerb::move: builder.moveTo(x + v[0], y + v[1]); break;
        case PathVerb::line: builder.lineTo(x + v[0], y + v[1]); break;
        case PathVerb::quadratic: builder.quadTo(x + v[0], y + v[1], x + v[2], y + v[3]); break;
        case PathVerb::cubic:
            builder.cubicTo(x + v[0], y + v[1], x + v[2], y + v[3], x + v[4], y + v[5]);
            break;
        case PathVerb::arc:
            // SVG's sweep flag 1 is clockwise in y-down space, Skia's kCW.
            builder.arcTo({v[0], v[1]}, v[2],
                          v[3] != 0.0F ? SkPathBuilder::kLarge_ArcSize
                                       : SkPathBuilder::kSmall_ArcSize,
                          v[4] != 0.0F ? SkPathDirection::kCW : SkPathDirection::kCCW,
                          {x + v[5], y + v[6]});
            break;
        case PathVerb::close: builder.close(); break;
        }
    }
    return builder.detach();
}

SkPaint::Join sk_join(StrokeJoin join) {
    switch (join) {
    case StrokeJoin::miter: return SkPaint::kMiter_Join;
    case StrokeJoin::round: return SkPaint::kRound_Join;
    case StrokeJoin::bevel: return SkPaint::kBevel_Join;
    }
    return SkPaint::kMiter_Join;
}

SkPaint::Cap sk_cap(StrokeCap cap) {
    switch (cap) {
    case StrokeCap::butt: return SkPaint::kButt_Cap;
    case StrokeCap::round: return SkPaint::kRound_Cap;
    case StrokeCap::square: return SkPaint::kSquare_Cap;
    }
    return SkPaint::kButt_Cap;
}

bool path_is_drawable(const PathGeometry& path, const Color& fill, std::string* reason) {
    if (!path_is_valid(path, reason)) return false;
    if (!std::isfinite(fill.red) || !std::isfinite(fill.green) || !std::isfinite(fill.blue) ||
        !std::isfinite(fill.alpha)) {
        set_error(reason, "the fill colour is not finite");
        return false;
    }
    return true;
}

bool draw_path(SkCanvas& canvas, const PaintCommand& command, float opacity,
               FrameResources& resources, std::string* error) {
    if (!command.path) {
        set_error(error, "path " + command.node_id + " has no geometry");
        return false;
    }
    const auto& path = *command.path;
    const auto& fill = command.fill;
    std::string reason;
    if (!path_is_drawable(path, fill, &reason)) {
        set_error(error, "path " + command.node_id + " cannot be drawn: " + reason);
        return false;
    }
    if (path.verbs.empty()) return true;
    const SkPath outline = sk_path(command);

    if (fill.alpha > 0.0F) {
        SkPaint paint;
        paint.setAntiAlias(path.antialias);
        paint.setColor4f(sk_color(fill, opacity));
        canvas.drawPath(outline, paint);
    }
    if (const auto box = path_gradient_box(command)) {
        for (const auto& declared : command.fill_gradients) {
            auto shader =
                linear_gradient_shader(resolve_linear_gradient(*box, declared), opacity,
                                       resources, error);
            if (!shader) return false;
            SkPaint paint;
            paint.setAntiAlias(path.antialias);
            paint.setShader(std::move(shader));
            canvas.drawPath(outline, paint);
        }
    }

    const auto& stroke = path.stroke;
    if (stroke.width > 0.0F && stroke.color.alpha > 0.0F) {
        SkPaint paint;
        paint.setAntiAlias(path.antialias);
        paint.setColor4f(sk_color(stroke.color, opacity));
        paint.setStyle(SkPaint::kStroke_Style);
        paint.setStrokeWidth(stroke.width);
        paint.setStrokeJoin(sk_join(stroke.join));
        paint.setStrokeCap(sk_cap(stroke.cap));
        paint.setStrokeMiter(stroke.miter_limit);
        if (!stroke.dash_array.empty()) {
            // An odd-length dash array repeats once, as in SVG.
            std::vector<SkScalar> intervals(stroke.dash_array.begin(), stroke.dash_array.end());
            if (intervals.size() % 2U != 0U) {
                intervals.insert(intervals.end(), stroke.dash_array.begin(),
                                 stroke.dash_array.end());
            }
            auto dashes = SkDashPathEffect::Make(intervals, stroke.dash_offset);
            if (!dashes) {
                set_error(error,
                          "Skia could not build the dash pattern of path " + command.node_id);
                return false;
            }
            paint.setPathEffect(std::move(dashes));
        }
        canvas.drawPath(outline, paint);
    }
    return true;
}

bool draw_image(SkCanvas& canvas, const PaintCommand& command, float opacity,
                FrameResources& resources, std::string* error) {
    const auto& box = command.bounds;
    if (!command.image) {
        set_error(error, "image " + command.node_id + " has no image reference");
        return false;
    }
    if (box.width <= 0.0F || box.height <= 0.0F) return true;
    const auto rect = SkRect::MakeXYWH(box.x, box.y, box.width, box.height);
    const auto clip = rounded_rect(rect, command.corner_radii);
    const auto fill_box = [&](const Color& color) {
        if (color.alpha <= 0.0F) return;
        SkPaint paint;
        paint.setAntiAlias(true);
        paint.setColor4f(sk_color(color, opacity));
        canvas.drawRRect(clip, paint);
    };
    fill_box(command.fill);
    if (!command.image_asset) {
        fill_box(kMissingImagePlaceholder);
        return true;
    }
    const auto& asset = *command.image_asset;
    const auto placement = image_placement(*command.image, asset.width, asset.height, box);
    if (!placement) return true;
    auto texture = image_asset_texture(command.image_asset, resources, error);
    if (!texture) return false;
    const int saved = canvas.save();
    if (is_rounded(command.corner_radii)) canvas.clipRRect(clip, true);
    SkPaint paint;
    paint.setAntiAlias(true);
    paint.setAlphaf(opacity);
    const auto sampling = command.image->sampling == ImageSampling::nearest
        ? SkSamplingOptions(SkFilterMode::kNearest)
        : SkSamplingOptions(SkFilterMode::kLinear);
    const auto& source = placement->source;
    const auto& destination = placement->destination;
    canvas.drawImageRect(texture.get(),
                         SkRect::MakeXYWH(source.x, source.y, source.width, source.height),
                         SkRect::MakeXYWH(destination.x, destination.y, destination.width,
                                          destination.height),
                         sampling, &paint, SkCanvas::kStrict_SrcRectConstraint);
    canvas.restoreToCount(saved);
    return true;
}

// Folding is exact when the command's paint operations do not overlap; text
// accepts the double blend of glyphs that overlap one another.
bool opacity_folds_into_paint(const PaintCommand& command) {
    if (command.kind == PaintCommand::Kind::rectangle) {
        const std::size_t fills =
            (command.fill.alpha > 0.0F ? 1U : 0U) + command.fill_gradients.size();
        return command.box_shadows.size() <= 1U && fills <= 1U;
    }
    if (command.kind == PaintCommand::Kind::path) {
        if (!command.path) return true;
        const auto& stroke = command.path->stroke;
        const bool stroked = stroke.width > 0.0F && stroke.color.alpha > 0.0F;
        const std::size_t operations = (command.fill.alpha > 0.0F ? 1U : 0U) +
                                       command.fill_gradients.size() + (stroked ? 1U : 0U);
        return operations <= 1U;
    }
    if (command.kind == PaintCommand::Kind::image) {
        // The image, or its placeholder, over a visible fill is two operations.
        return command.fill.alpha <= 0.0F;
    }
    if (command.kind == PaintCommand::Kind::text) {
        if (command.text_runs.empty()) return !command.underline && !command.strikethrough;
        return std::none_of(command.text_runs.begin(), command.text_runs.end(),
            [](const TextRun& run) { return run.style.underline || run.style.strikethrough; });
    }
    return false;
}

bool paint_command(SkCanvas& canvas, const PaintCommand& command,
                   std::string_view directory, FrameResources& resources,
                   std::string* error, ParagraphSlot* paragraph = nullptr) {
    if (command.kind == PaintCommand::Kind::begin_layer) {
        const float alpha = std::clamp(command.opacity, 0.0F, 1.0F);
        if (command.layer_unbounded) {
            canvas.saveLayerAlphaf(nullptr, alpha);
        } else {
            const auto bounds = layer_rect(command.bounds);
            canvas.saveLayerAlphaf(&bounds, alpha);
        }
        ++resources.layers;
        return true;
    }
    if (command.kind == PaintCommand::Kind::end_layer) {
        canvas.restore();
        return true;
    }
    if (command.kind == PaintCommand::Kind::text &&
        command.text.empty() && command.text_runs.empty()) {
        return true;
    }
    if (!draws_content(command.kind)) return true;
    const float opacity = std::clamp(command.opacity, 0.0F, 1.0F);
    if (opacity <= 0.0F) return true;

    const int saved = canvas.save();
    if (!is_identity(command.transform)) canvas.concat(sk_matrix(command.transform));
    const bool layered = opacity < 1.0F && !opacity_folds_into_paint(command);
    const float paint_opacity = layered ? 1.0F : opacity;
    if (layered && command.kind != PaintCommand::Kind::text) {
        const auto bounds = layer_rect(paint_extent(command).rect);
        canvas.saveLayerAlphaf(&bounds, opacity);
        ++resources.layers;
    }
    bool drawn = true;
    if (command.kind == PaintCommand::Kind::mesh) {
        drawn = draw_mesh(canvas, command, resources, error);
    } else if (command.kind == PaintCommand::Kind::rectangle) {
        drawn = draw_rectangle(canvas, command, paint_opacity, resources, error);
    } else if (command.kind == PaintCommand::Kind::path) {
        drawn = draw_path(canvas, command, paint_opacity, resources, error);
    } else if (command.kind == PaintCommand::Kind::image) {
        drawn = draw_image(canvas, command, paint_opacity, resources, error);
    } else {
        drawn = draw_text(canvas, command, paint_opacity, layered ? opacity : 1.0F,
                          directory, resources, paragraph, error);
    }
    canvas.restoreToCount(saved);
    return drawn;
}

using Extent = std::optional<PixelRect>;

constexpr std::int32_t kExtentMargin = 2;

Extent pixel_extent(const std::optional<Rect>& painted, float scale) {
    if (!painted) return PixelRect{};
    const auto pixels = to_pixel_rect(*painted, scale);
    if (!pixels) return std::nullopt;
    if (pixels->empty()) return PixelRect{};
    return PixelRect{pixels->left - kExtentMargin, pixels->top - kExtentMargin,
                     pixels->right + kExtentMargin, pixels->bottom + kExtentMargin};
}

bool sane_box(const Rect& rect) {
    return std::isfinite(rect.x) && std::isfinite(rect.y) &&
        std::isfinite(rect.width) && std::isfinite(rect.height) &&
        rect.width >= 0.0F && rect.height >= 0.0F;
}

// A command whose box is not finite has no known extent and is always drawn.
Extent command_extent(const PaintCommand& command, const Rect& frame, float scale) {
    static const DamagePropagation propagation;
    if (!sane_box(command.bounds) || !draws_content(command.kind)) return std::nullopt;
    return pixel_extent(command_paint_bounds(command, frame, propagation), scale);
}

std::vector<Extent> paint_command_extents(
    const Scene& scene, const std::vector<PaintCommand>& commands, float scale) {
    const Rect frame{0.0F, 0.0F, scene.width, scene.height};
    std::vector<Extent> extents;
    extents.reserve(commands.size());
    for (const auto& command : commands) {
        extents.push_back(command_extent(command, frame, scale));
    }
    return extents;
}

bool same_color(const Color& a, const Color& b) {
    return std::memcmp(&a, &b, sizeof(Color)) == 0;
}

struct GpuTimer final {
    std::uint64_t frame = 0;
    bool available = false;
    std::uint64_t nanoseconds = 0;
};

struct GpuTimerContext final {
    std::shared_ptr<GpuTimer> timer;
    std::uint64_t frame = 0;
};

void record_gpu_time(skgpu::graphite::GpuFinishedContext context,
                     skgpu::CallbackResult result, const skgpu::GpuStats& stats) {
    std::unique_ptr<GpuTimerContext> owner(static_cast<GpuTimerContext*>(context));
    if (!owner || !owner->timer || result != skgpu::CallbackResult::kSuccess) return;
    if (owner->frame < owner->timer->frame) return;
    owner->timer->frame = owner->frame;
    owner->timer->available = stats.elapsedTime > 0;
    owner->timer->nanoseconds = stats.elapsedTime;
}

}  // namespace

std::string_view to_string(FullRepaintReason reason) noexcept {
    switch (reason) {
        case FullRepaintReason::none: return "none";
        case FullRepaintReason::full_render: return "full_render";
        case FullRepaintReason::first_frame: return "first_frame";
        case FullRepaintReason::surface_recreated: return "surface_recreated";
        case FullRepaintReason::previous_frame_failed: return "previous_frame_failed";
        case FullRepaintReason::scene_version: return "scene_version";
        case FullRepaintReason::frame_changed: return "frame_changed";
        case FullRepaintReason::reload: return "reload";
        case FullRepaintReason::full_damage: return "full_damage";
        case FullRepaintReason::damage_threshold: return "damage_threshold";
        case FullRepaintReason::frame_mismatch: return "frame_mismatch";
        case FullRepaintReason::damage_model_version: return "damage_model_version";
        case FullRepaintReason::damage_frame_mismatch: return "damage_frame_mismatch";
    }
    return "unknown";
}

class SkiaDawnSurface::Impl final {
public:
    ~Impl() {
        frame_surface_.reset();
        retained_image_.reset();
        retained_surface_.reset();
        recorder_.reset();
        if (context_) {
            context_->submit(skgpu::graphite::SyncToCpu::kYes);
            context_->checkAsyncWorkCompletion();
        }
        context_.reset();
        current_texture_ = nullptr;
        surface_ = nullptr;
        queue_ = nullptr;
        device_ = nullptr;
        adapter_ = nullptr;
        instance_ = nullptr;
        native_instance_.reset();
    }

    bool initialize(const Config& requested, std::string* error) {
        config_ = requested;
        if (config_.width == 0 || config_.height == 0 ||
            !std::isfinite(config_.scale) || config_.scale <= 0.0F) {
            set_error(error, "GPU surface dimensions and scale must be positive");
            return false;
        }

        if (!dawn_bootstrap_is_registered(error)) {
            return false;
        }

        wgpu::InstanceDescriptor instance_descriptor{};
        const wgpu::InstanceFeatureName instance_features[] = {
            wgpu::InstanceFeatureName::TimedWaitAny,
        };
        instance_descriptor.requiredFeatureCount = 1;
        instance_descriptor.requiredFeatures = instance_features;
        native_instance_ = std::make_unique<dawn::native::Instance>(
            reinterpret_cast<const WGPUInstanceDescriptor*>(&instance_descriptor));
        instance_ = wgpu::Instance(native_instance_->Get());
        if (!instance_) {
            set_error(error, "Dawn could not create an instance");
            return false;
        }

        if (config_.native_surface_handle != nullptr) {
            wgpu::SurfaceSourceMetalLayer source{};
            source.layer = config_.native_surface_handle;
            wgpu::SurfaceDescriptor descriptor{};
            descriptor.nextInChain = &source;
            surface_ = instance_.CreateSurface(&descriptor);
            if (!surface_) {
                set_error(error, "Dawn could not create a Metal surface from CAMetalLayer");
                return false;
            }
        }

        wgpu::RequestAdapterOptions adapter_options{};
        adapter_options.powerPreference = wgpu::PowerPreference::HighPerformance;
        adapter_options.backendType = wgpu::BackendType::Metal;
        if (surface_) {
            adapter_options.compatibleSurface = surface_;
        }
        wgpu::RequestAdapterStatus adapter_status = wgpu::RequestAdapterStatus::Unavailable;
        std::string adapter_message;
        instance_.RequestAdapter(
            &adapter_options,
            wgpu::CallbackMode::AllowProcessEvents,
            [&](wgpu::RequestAdapterStatus status, wgpu::Adapter adapter,
                wgpu::StringView message) {
                adapter_status = status;
                adapter_message = string_view(message);
                if (status == wgpu::RequestAdapterStatus::Success) {
                    adapter_ = std::move(adapter);
                }
            });
        instance_.ProcessEvents();
        if (adapter_status != wgpu::RequestAdapterStatus::Success || !adapter_) {
            set_error(error, "Dawn Metal adapter unavailable: " + adapter_message);
            return false;
        }

        wgpu::DeviceDescriptor device_descriptor{};
        device_descriptor.label = "Vellum Skia/Dawn Device";
        const wgpu::FeatureName timestamp_feature[] = {wgpu::FeatureName::TimestampQuery};
        if (adapter_.HasFeature(wgpu::FeatureName::TimestampQuery)) {
            device_descriptor.requiredFeatureCount = 1;
            device_descriptor.requiredFeatures = timestamp_feature;
        }
        device_descriptor.SetDeviceLostCallback(
            wgpu::CallbackMode::AllowSpontaneous,
            [lost = device_lost_](const wgpu::Device&, wgpu::DeviceLostReason reason,
                                  wgpu::StringView) {
                if (reason != wgpu::DeviceLostReason::Destroyed) {
                    lost->store(true, std::memory_order_release);
                }
            });
        wgpu::RequestDeviceStatus device_status = wgpu::RequestDeviceStatus::Error;
        std::string device_message;
        adapter_.RequestDevice(
            &device_descriptor,
            wgpu::CallbackMode::AllowProcessEvents,
            [&](wgpu::RequestDeviceStatus status, wgpu::Device device,
                wgpu::StringView message) {
                device_status = status;
                device_message = string_view(message);
                if (status == wgpu::RequestDeviceStatus::Success) {
                    device_ = std::move(device);
                }
            });
        instance_.ProcessEvents();
        if (device_status != wgpu::RequestDeviceStatus::Success || !device_) {
            set_error(error, "Dawn Metal device unavailable: " + device_message);
            return false;
        }
        queue_ = device_.GetQueue();

        skgpu::graphite::DawnBackendContext backend_context;
        backend_context.fInstance = instance_;
        backend_context.fDevice = device_;
        backend_context.fQueue = queue_;
        skgpu::graphite::ContextOptions context_options;
        context_ = skgpu::graphite::ContextFactory::MakeDawn(
            backend_context, context_options);
        if (!context_) {
            set_error(error, "Skia Graphite could not use the Dawn device");
            return false;
        }
        recorder_ = context_->makeRecorder();
        if (!recorder_) {
            set_error(error, "Skia Graphite could not create a recorder");
            return false;
        }

        if (surface_ && !configure_surface(error)) {
            return false;
        }
        if (!create_retained(error)) {
            return false;
        }
        note_configured();
        retained_reason_ = FullRepaintReason::first_frame;
        gpu_stats_supported_ = static_cast<bool>(
            context_->supportedGpuStats() & skgpu::GpuStatsFlags::kElapsedTime);

        wgpu::AdapterInfo adapter_info{};
        adapter_.GetInfo(&adapter_info);
        evidence_.available = true;
        evidence_.native_surface = surface_ != nullptr;
        evidence_.fallback = false;
        evidence_.renderer = "Skia Graphite";
        evidence_.backend = backend_name(adapter_info.backendType);
        evidence_.adapter = string_view(adapter_info.device);
        if (evidence_.adapter.empty()) {
            evidence_.adapter = "Dawn " + evidence_.backend + " adapter";
        }
        evidence_.texture_format = "bgra8unorm";
        if (evidence_.backend != "Metal") {
            set_error(error, "requested Metal backend but Dawn selected " + evidence_.backend);
            return false;
        }
        return true;
    }

    bool resize(std::uint32_t width, std::uint32_t height, float scale,
                std::string* error) {
        if (width == 0 || height == 0 || !std::isfinite(scale) || scale <= 0.0F) {
            set_error(error, "GPU surface dimensions and scale must be positive");
            return false;
        }
        config_.width = width;
        config_.height = height;
        config_.scale = scale;
        frame_surface_.reset();
        retained_valid_ = false;
        retained_reason_ = FullRepaintReason::surface_recreated;
        if (surface_ && !configure_surface(error)) {
            return false;
        }
        if (!create_retained(error)) return false;
        note_configured();
        trim_when_settled_ = true;
        trim_since_frame_ = caches_.frame + 1U;
        return true;
    }

    bool render(const Scene& scene, std::string* error) {
        if (!accepts_with_diagnostics(scene, error)) return false;
        discard_tracked_damage(scene);
        return draw_frame(scene, FullRepaintReason::full_render, nullptr, error);
    }

    bool render_incremental(const Scene& scene, std::string* error) {
        if (!accepts_with_diagnostics(scene, error)) return false;
        tracker_.commit(scene, config_.scale);
        const auto damage = tracker_.take_damage();
        return draw_frame(scene, FullRepaintReason::none, &damage, error);
    }

    bool render(const Scene& scene, const DamageRegion& damage, std::string* error) {
        if (!accepts_with_diagnostics(scene, error)) return false;
        const auto invalidated = tracker_.take_damage();
        discard_tracked_damage(scene);
        const auto frame = frame_rect(scene, config_.scale);
        const bool unframed_nothing = damage.frame().empty() && damage.empty();
        if (!unframed_nothing && damage.frame() != frame) {
            return draw_frame(scene, FullRepaintReason::damage_frame_mismatch, nullptr, error);
        }
        DamageRegion merged(frame, damage.frame().empty() ? DamagePolicy{} : damage.policy());
        merged.add(damage);
        merged.add(invalidated);
        return draw_frame(scene, FullRepaintReason::none, &merged, error);
    }

    void simulate_device_loss() {
        if (!device_) return;
        device_.ForceLoss(wgpu::DeviceLostReason::Unknown, "Vellum simulated device loss");
        instance_.ProcessEvents();
    }

    void invalidate(InvalidationReason reason, std::string_view node_id) {
        if (reason == InvalidationReason::reload) reload_pending_ = true;
        tracker_.invalidate(reason, node_id);
    }

    FrameStats frame_stats() const {
        FrameStats stats = stats_;
        if (gpu_timer_ && gpu_timer_->frame == stats.frame && gpu_timer_->available) {
            stats.gpu_time_available = true;
            stats.gpu_milliseconds = static_cast<double>(gpu_timer_->nanoseconds) / 1.0e6;
        }
        return stats;
    }

    void set_cache_budgets(const RenderCacheBudgets& budgets) {
        if (caches_.set_budgets(budgets) > 0U) note_trim(CacheTrimReason::budget_changed);
    }

    RenderCacheBudgets cache_budgets() const { return caches_.budgets(); }

    const std::vector<LoweringDiagnostic>& diagnostics() const noexcept { return diagnostics_; }

    RenderTelemetry telemetry() const {
        const auto frame = frame_stats();
        RenderTelemetry telemetry;
        telemetry.frame = frame.frame;
        telemetry.full_repaint = frame.full_repaint;
        telemetry.full_repaint_reason = std::string(to_string(frame.full_repaint_reason));
        telemetry.damage_area_pixels = frame.damage_area_pixels;
        telemetry.frame_area_pixels = frame.frame_area_pixels;
        telemetry.damage_fraction = frame.damage_fraction;
        telemetry.paint_commands_total = frame.paint_commands_total;
        telemetry.paint_commands_recorded = frame.paint_commands_recorded;
        telemetry.layer_count = layer_count_;
        telemetry.paragraphs = caches_.paragraphs.stats();
        telemetry.gradients = caches_.gradients.stats();
        telemetry.textures = caches_.textures.stats();
        telemetry.atlas_bytes = telemetry.textures.bytes;
        telemetry.gpu_time_available = frame.gpu_time_available;
        telemetry.gpu_milliseconds = frame.gpu_milliseconds;
        telemetry.cpu_milliseconds = frame.cpu_milliseconds;
        telemetry.resize_settled = resize_state_.settled();
        telemetry.trims = trims_;
        telemetry.last_trim = last_trim_;
        return telemetry;
    }

    bool finish(std::string* error) {
        if (!context_) {
            set_error(error, "Skia/Dawn surface is not initialized");
            return false;
        }
        context_->submit(skgpu::graphite::SyncToCpu::kYes);
        context_->checkAsyncWorkCompletion();
        instance_.ProcessEvents();
        if (device_lost()) {
            return fail_device_lost(error, "the Dawn device was lost");
        }
        return true;
    }

    // Records what the frame loses through `accept_scene`, the decision every
    // backend shares.
    bool accepts_with_diagnostics(const Scene& scene, std::string* error) {
        auto acceptance = accept_scene(scene);
        diagnostics_ = std::move(acceptance.diagnostics);
        if (acceptance.accepted) return true;
        set_error(error, std::move(acceptance.reason));
        return false;
    }

    bool capture_png(std::vector<std::uint8_t>& png, std::string* error) {
        std::vector<std::uint8_t> rgba;
        std::uint32_t pixel_width = 0;
        std::uint32_t pixel_height = 0;
        if (!capture_rgba(rgba, pixel_width, pixel_height, error)) {
            return false;
        }
        const auto image_info = SkImageInfo::Make(
            static_cast<int>(pixel_width), static_cast<int>(pixel_height),
            kRGBA_8888_SkColorType,
            kPremul_SkAlphaType, SkColorSpace::MakeSRGB());
        const SkPixmap pixmap(image_info, rgba.data(),
                             static_cast<std::size_t>(pixel_width) * 4U);
        SkDynamicMemoryWStream stream;
        SkPngEncoder::Options options;
        if (!SkPngEncoder::Encode(&stream, pixmap, options)) {
            set_error(error, "Skia could not encode the captured frame as PNG");
            return false;
        }
        auto data = stream.detachAsData();
        if (!data || data->isEmpty()) {
            set_error(error, "Skia returned an empty PNG capture");
            return false;
        }
        const auto* begin = static_cast<const std::uint8_t*>(data->data());
        png.assign(begin, begin + data->size());
        return true;
    }

    bool capture_rgba(std::vector<std::uint8_t>& rgba,
                      std::uint32_t& pixel_width,
                      std::uint32_t& pixel_height,
                      std::string* error) {
        if (!last_scene_.has_value()) {
            set_error(error, "capture requires one successfully submitted scene");
            return false;
        }
        if (device_lost()) {
            return fail_device_lost(
                error, "the Dawn device was lost; the last good frame can no longer be read back");
        }
        if (!retained_valid_) {
            const Scene scene = *last_scene_;
            const auto saved = stats_;
            const auto saved_layers = layer_count_;
            const auto saved_caches = caches_.frame_state();
            const bool reload = reload_pending_;
            internal_redraw_ = true;
            const bool redrawn = draw_frame(scene, FullRepaintReason::full_render, nullptr, error);
            internal_redraw_ = false;
            stats_ = saved;
            layer_count_ = saved_layers;
            caches_.restore(saved_caches);
            reload_pending_ = reload;
            if (!redrawn) return false;
            tracker_.commit(scene, config_.scale);
        }
        if (!read_frame(rgba, error)) {
            return false;
        }
        pixel_width = static_cast<std::uint32_t>(physical_width());
        pixel_height = static_cast<std::uint32_t>(physical_height());
        return true;
    }

    const GpuEvidence& evidence() const noexcept { return evidence_; }

private:
    int physical_width() const {
        return std::max(1, static_cast<int>(
            std::lround(static_cast<double>(config_.width) * config_.scale)));
    }

    int physical_height() const {
        return std::max(1, static_cast<int>(
            std::lround(static_cast<double>(config_.height) * config_.scale)));
    }

    bool configure_surface(std::string* error) {
        wgpu::SurfaceCapabilities capabilities{};
        surface_.GetCapabilities(adapter_, &capabilities);
        preferred_format_ = wgpu::TextureFormat::Undefined;
        for (std::size_t index = 0; index < capabilities.formatCount; ++index) {
            if (capabilities.formats[index] == wgpu::TextureFormat::BGRA8Unorm) {
                preferred_format_ = wgpu::TextureFormat::BGRA8Unorm;
                break;
            }
        }
        if (preferred_format_ == wgpu::TextureFormat::Undefined) {
            set_error(error, "Dawn surface does not support the required BGRA8Unorm format");
            return false;
        }

        preferred_mode_ = wgpu::PresentMode::Fifo;
        if (!config_.vsync) {
            for (std::size_t index = 0; index < capabilities.presentModeCount; ++index) {
                if (capabilities.presentModes[index] == wgpu::PresentMode::Immediate) {
                    preferred_mode_ = wgpu::PresentMode::Immediate;
                    break;
                }
            }
        }
        wgpu::SurfaceConfiguration configuration{};
        configuration.device = device_;
        configuration.format = preferred_format_;
        configuration.width = static_cast<std::uint32_t>(physical_width());
        configuration.height = static_cast<std::uint32_t>(physical_height());
        configuration.presentMode = preferred_mode_;
        configuration.usage = wgpu::TextureUsage::RenderAttachment |
                              wgpu::TextureUsage::TextureBinding |
                              wgpu::TextureUsage::CopySrc;
        surface_.Configure(&configuration);
        evidence_.texture_format = "bgra8unorm";
        return true;
    }

    bool create_retained(std::string* error) {
        retained_image_.reset();
        retained_surface_.reset();
        retained_valid_ = false;
        const auto image_info = surface_
            ? SkImageInfo::Make(physical_width(), physical_height(), kBGRA_8888_SkColorType,
                                kPremul_SkAlphaType, SkColorSpace::MakeSRGB())
            : SkImageInfo::MakeN32Premul(physical_width(), physical_height());
        retained_surface_ = SkSurfaces::RenderTarget(recorder_.get(), image_info);
        if (!retained_surface_) {
            set_error(error, "Skia Graphite could not allocate the retained GPU surface");
            return false;
        }
        if (surface_) {
            retained_image_ = SkSurfaces::AsImage(retained_surface_);
            if (!retained_image_) {
                retained_surface_.reset();
                set_error(error, "Skia Graphite could not sample the retained GPU surface");
                return false;
            }
        }
        return true;
    }

    bool device_lost() const {
        return device_lost_->load(std::memory_order_acquire) ||
            (context_ && context_->isDeviceLost());
    }

    // Records that the surface now matches its size, so the frame after it
    // restarts the settle count.
    void note_configured() {
        resize_state_.request(static_cast<std::uint32_t>(physical_width()),
                              static_cast<std::uint32_t>(physical_height()));
        resize_state_.mark_configured();
        frame_configured_ = true;
    }

    void note_trim(CacheTrimReason reason) {
        ++trims_;
        last_trim_ = reason;
    }

    // Called after each frame is submitted. Once a resize has settled, entries
    // no frame has used since the resize, such as gradients sized for the
    // sizes a live resize passed through, are dropped. A capture's internal
    // redraw is not a presented frame and does not count.
    void note_frame_presented() {
        if (internal_redraw_) return;
        resize_state_.note_frame(frame_configured_);
        frame_configured_ = false;
        if (trim_when_settled_ && resize_state_.settled()) {
            trim_when_settled_ = false;
            caches_.trim_unused_since(trim_since_frame_);
            note_trim(CacheTrimReason::resize_settled);
        }
    }

    bool fail_device_lost(std::string* error, std::string message) {
        // Every cached texture belonged to the lost device.
        if (!device_loss_trimmed_) {
            device_loss_trimmed_ = true;
            caches_.clear();
            note_trim(CacheTrimReason::device_lost);
        }
        retained_valid_ = false;
        retained_reason_ = FullRepaintReason::previous_frame_failed;
        set_error(error, std::move(message));
        return false;
    }

    PixelRect target_rect() const {
        return {0, 0, physical_width(), physical_height()};
    }

    void discard_tracked_damage(const Scene& scene) {
        tracker_.commit(scene, config_.scale);
        (void)tracker_.take_damage();
    }

    FullRepaintReason full_repaint_reason(
        const Scene& scene, const DamageRegion& damage,
        std::vector<PixelRect>& rects) const {
        if (!retained_valid_) return retained_reason_;
        if (!last_scene_) return FullRepaintReason::first_frame;
        if (!partial_redraw_supported(scene.scene_version)) {
            return FullRepaintReason::damage_model_version;
        }
        if (last_scene_->scene_version != scene.scene_version) {
            return FullRepaintReason::scene_version;
        }
        if (std::memcmp(&last_scene_->width, &scene.width, sizeof(float)) != 0 ||
            std::memcmp(&last_scene_->height, &scene.height, sizeof(float)) != 0 ||
            !same_color(last_scene_->background, scene.background)) {
            return FullRepaintReason::frame_changed;
        }
        if (reload_pending_) return FullRepaintReason::reload;
        const auto target = target_rect();
        const auto frame = frame_rect(scene, config_.scale);
        if (frame.left > target.left || frame.top > target.top ||
            frame.right < target.right || frame.bottom < target.bottom) {
            return FullRepaintReason::frame_mismatch;
        }
        const auto stats = damage.stats();
        if (damage.is_full_frame()) {
            return stats.collapsed_to_full_frame && !stats.explicit_full_frame
                ? FullRepaintReason::damage_threshold
                : FullRepaintReason::full_damage;
        }
        std::int64_t area = 0;
        for (const auto& rect : damage.rects()) {
            const auto clipped = intersection(rect, target);
            if (clipped.empty()) continue;
            rects.push_back(clipped);
            area += clipped.area();
        }
        if (area >= target.area()) return FullRepaintReason::full_damage;
        return FullRepaintReason::none;
    }

    bool draw_frame(const Scene& scene, FullRepaintReason forced,
                    const DamageRegion* damage, std::string* error) {
        const auto started = std::chrono::steady_clock::now();
        if (!context_ || !recorder_ || !evidence_.available || !retained_surface_) {
            set_error(error, "Skia/Dawn surface is not initialized");
            return false;
        }
        if (device_lost()) {
            return fail_device_lost(error, "the Dawn device was lost");
        }

        std::vector<PixelRect> rects;
        FullRepaintReason reason = forced;
        if (reason == FullRepaintReason::none && damage != nullptr) {
            reason = full_repaint_reason(scene, *damage, rects);
        } else if (reason == FullRepaintReason::none) {
            reason = FullRepaintReason::full_render;
        }
        const bool full = reason != FullRepaintReason::none;
        const auto target = target_rect();

        FrameStats stats;
        stats.frame = stats_.frame + 1;
        stats.full_repaint = full;
        stats.full_repaint_reason = reason;
        stats.frame_area_pixels = target.area();
        if (damage != nullptr) {
            const auto damage_stats = damage->stats();
            stats.damage_rects_before_coalescing = damage_stats.rects_before_coalescing;
            stats.damage_rects_after_coalescing = damage_stats.rects_after_coalescing;
        }
        if (full) {
            stats.damage_area_pixels = target.area();
            stats.damage_rects_after_coalescing = 1;
            stats.damage_rects_before_coalescing =
                std::max<std::size_t>(1, stats.damage_rects_before_coalescing);
        } else {
            for (const auto& rect : rects) stats.damage_area_pixels += rect.area();
            stats.damage_rects_after_coalescing = rects.size();
        }
        stats.damage_fraction = stats.frame_area_pixels > 0
            ? static_cast<double>(stats.damage_area_pixels) /
                  static_cast<double>(stats.frame_area_pixels)
            : 0.0;

        retained_valid_ = false;
        retained_reason_ = FullRepaintReason::previous_frame_failed;
        const auto commands = make_paint_commands(scene);
        stats.paint_commands_total = commands.size();
        auto* canvas = retained_surface_->getCanvas();
        caches_.begin_frame();
        FrameResources resources{.meshes = {.recorder = recorder_.get()}, .caches = &caches_};
        // A texture cached by a frame that is never submitted was never
        // written on the GPU; drop it so a later frame uploads it again.
        struct UploadGuard final {
            detail::SkiaResourceCaches& caches;
            const FrameResources& resources;
            bool submitted = false;
            ~UploadGuard() {
                if (submitted) return;
                for (const auto& key : resources.uploaded) caches.textures.erase(key);
            }
        } upload_guard{caches_, resources};
        if (full) {
            const int saved = canvas->save();
            canvas->resetMatrix();
            canvas->clear(sk_color(scene.background));
            canvas->scale(config_.scale, config_.scale);
            for (const auto& command : commands) {
                if (!paint_command(*canvas, command, config_.font_directory,
                                   resources, error)) {
                    canvas->restoreToCount(saved);
                    return false;
                }
            }
            canvas->restoreToCount(saved);
            stats.paint_commands_recorded = commands.size();
            stats.paint_command_draws = commands.size();
        } else if (!rects.empty()) {
            const auto extents = paint_command_extents(scene, commands, config_.scale);
            std::vector<bool> drawn(commands.size(), false);
            std::vector<ParagraphSlot> paragraphs(commands.size());
            for (const auto& rect : rects) {
                const int saved = canvas->save();
                canvas->resetMatrix();
                canvas->clipRect(
                    SkRect::Make(SkIRect::MakeLTRB(rect.left, rect.top, rect.right, rect.bottom)),
                    SkClipOp::kIntersect, false);
                canvas->clear(sk_color(scene.background));
                canvas->scale(config_.scale, config_.scale);
                for (std::size_t index = 0; index < commands.size(); ++index) {
                    if (extents[index] && !overlaps(*extents[index], rect)) continue;
                    if (!paint_command(*canvas, commands[index], config_.font_directory,
                                       resources, error, &paragraphs[index])) {
                        canvas->restoreToCount(saved);
                        return false;
                    }
                    drawn[index] = true;
                    ++stats.paint_command_draws;
                }
                canvas->restoreToCount(saved);
            }
            stats.paint_commands_recorded =
                static_cast<std::size_t>(std::count(drawn.begin(), drawn.end(), true));
        }

        if (!submit_frame(&stats, nullptr, error)) return false;
        upload_guard.submitted = true;
        layer_count_ = resources.layers;
        note_frame_presented();
        retained_valid_ = true;
        retained_reason_ = FullRepaintReason::none;
        reload_pending_ = false;
        last_scene_ = scene;
        stats.cpu_milliseconds = std::chrono::duration<double, std::milli>(
            std::chrono::steady_clock::now() - started).count();
        stats_ = stats;
        return true;
    }

    bool acquire_frame_surface(std::string* error) {
        frame_surface_.reset();
        wgpu::SurfaceTexture texture{};
        surface_.GetCurrentTexture(&texture);
        if (texture.status != wgpu::SurfaceGetCurrentTextureStatus::SuccessOptimal &&
            texture.status != wgpu::SurfaceGetCurrentTextureStatus::SuccessSuboptimal) {
            set_error(error, "Dawn could not acquire the native surface texture");
            return false;
        }
        current_texture_ = std::move(texture.texture);
        const auto backend_texture =
            skgpu::graphite::BackendTextures::MakeDawn(current_texture_.Get());
        if (!backend_texture.isValid()) {
            set_error(error, "Skia rejected Dawn's native surface texture");
            current_texture_ = nullptr;
            return false;
        }
        frame_surface_ = SkSurfaces::WrapBackendTexture(
            recorder_.get(), backend_texture, kBGRA_8888_SkColorType,
            SkColorSpace::MakeSRGB(), nullptr);
        if (!frame_surface_) {
            set_error(error, "Skia could not wrap Dawn's native surface texture");
            current_texture_ = nullptr;
            return false;
        }
        return true;
    }

    bool submit_frame(FrameStats* stats, std::vector<std::uint8_t>* rgba,
                      std::string* error) {
        if (surface_) {
            const auto waiting = std::chrono::steady_clock::now();
            if (!acquire_frame_surface(error)) return false;
            if (stats != nullptr) {
                stats->present_wait_milliseconds = std::chrono::duration<double, std::milli>(
                    std::chrono::steady_clock::now() - waiting).count();
            }
            SkPaint copy;
            copy.setBlendMode(SkBlendMode::kSrc);
            frame_surface_->getCanvas()->drawImage(
                retained_image_, 0.0F, 0.0F, SkSamplingOptions(), &copy);
        }

        auto recording = recorder_->snap();
        if (!recording) {
            set_error(error, "Skia Graphite produced no recording");
            current_texture_ = nullptr;
            frame_surface_.reset();
            return false;
        }
        skgpu::graphite::InsertRecordingInfo insertion{};
        insertion.fRecording = recording.get();
        if (gpu_stats_supported_ && stats != nullptr) {
            if (!gpu_timer_) gpu_timer_ = std::make_shared<GpuTimer>();
            insertion.fGpuStatsFlags = skgpu::GpuStatsFlags::kElapsedTime;
            insertion.fFinishedContext = new GpuTimerContext{gpu_timer_, stats->frame};
            insertion.fFinishedWithStatsProc = record_gpu_time;
        }
        if (!context_->insertRecording(insertion)) {
            set_error(error, "Skia Graphite could not insert the frame recording");
            current_texture_ = nullptr;
            frame_surface_.reset();
            return false;
        }
        context_->submit(skgpu::graphite::SyncToCpu::kNo);

        if (rgba != nullptr) {
            SkSurface& source = surface_ ? *frame_surface_ : *retained_surface_;
            if (!read_target_rgba(source, *rgba, error)) {
                current_texture_ = nullptr;
                frame_surface_.reset();
                return false;
            }
        }

        if (surface_) {
            surface_.Present();
            current_texture_ = nullptr;
            frame_surface_.reset();
            if (stats != nullptr) stats->presented = true;
        }
        instance_.ProcessEvents();
        return true;
    }

    bool read_frame(std::vector<std::uint8_t>& rgba, std::string* error) {
        return submit_frame(nullptr, &rgba, error);
    }

    bool read_target_rgba(
        SkSurface& target, std::vector<std::uint8_t>& rgba, std::string* error) {
        const auto pixel_width = static_cast<std::uint32_t>(physical_width());
        const auto pixel_height = static_cast<std::uint32_t>(physical_height());
        const auto byte_count = checked_image_byte_count(
            pixel_width, pixel_height, 4U);
        if (!byte_count) {
            set_error(error, "GPU readback dimensions exceed addressable storage");
            return false;
        }
        const auto image_info = SkImageInfo::Make(
            physical_width(), physical_height(), kRGBA_8888_SkColorType,
            kPremul_SkAlphaType, SkColorSpace::MakeSRGB());
        const auto row_bytes = static_cast<std::size_t>(pixel_width) * 4U;

        struct ReadbackState final {
            std::size_t row_bytes = 0;
            std::size_t byte_count = 0;
            std::vector<std::uint8_t> pixels;
            std::atomic_bool finished{false};
            bool ok = false;
        };
        auto state = std::make_shared<ReadbackState>();
        state->row_bytes = row_bytes;
        state->byte_count = *byte_count;

        auto callback = [](SkImage::ReadPixelsContext context,
                           std::unique_ptr<const SkImage::AsyncReadResult> result) {
            std::unique_ptr<std::shared_ptr<ReadbackState>> owner(
                static_cast<std::shared_ptr<ReadbackState>*>(context));
            auto state = owner ? *owner : nullptr;
            if (!state) return;
            if (!result || result->count() < 1 || result->data(0) == nullptr ||
                result->rowBytes(0) < state->row_bytes) {
                state->finished.store(true, std::memory_order_release);
                return;
            }
            const auto* source = static_cast<const std::uint8_t*>(result->data(0));
            state->pixels.resize(state->byte_count);
            const auto height = state->byte_count / state->row_bytes;
            for (std::size_t y = 0; y < height; ++y) {
                std::memcpy(
                    state->pixels.data() + y * state->row_bytes,
                    source + y * result->rowBytes(0),
                    state->row_bytes);
            }
            state->ok = true;
            state->finished.store(true, std::memory_order_release);
        };

        auto* callback_state = new std::shared_ptr<ReadbackState>(state);
        context_->asyncRescaleAndReadPixels(
            &target,
            image_info,
            SkIRect::MakeWH(physical_width(), physical_height()),
            SkImage::RescaleGamma::kSrc,
            SkImage::RescaleMode::kNearest,
            callback,
            callback_state);
        context_->submit(skgpu::graphite::SyncToCpu::kYes);

        const auto deadline =
            std::chrono::steady_clock::now() + std::chrono::seconds(5);
        while (!state->finished.load(std::memory_order_acquire) &&
               std::chrono::steady_clock::now() < deadline) {
            context_->checkAsyncWorkCompletion();
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        if (!state->finished.load(std::memory_order_acquire)) {
            set_error(error, "Skia GPU readback timed out");
            return false;
        }
        if (!state->ok) {
            set_error(error, "Skia GPU readback completed without pixels");
            return false;
        }
        rgba = std::move(state->pixels);
        return true;
    }

    Config config_{};
    GpuEvidence evidence_{};
    std::optional<Scene> last_scene_;
    std::unique_ptr<dawn::native::Instance> native_instance_;
    wgpu::Instance instance_;
    wgpu::Adapter adapter_;
    wgpu::Device device_;
    wgpu::Queue queue_;
    wgpu::Surface surface_;
    wgpu::Texture current_texture_;
    wgpu::TextureFormat preferred_format_ = wgpu::TextureFormat::Undefined;
    wgpu::PresentMode preferred_mode_ = wgpu::PresentMode::Fifo;
    std::unique_ptr<skgpu::graphite::Context> context_;
    std::unique_ptr<skgpu::graphite::Recorder> recorder_;
    sk_sp<SkSurface> frame_surface_;
    sk_sp<SkSurface> retained_surface_;
    sk_sp<SkImage> retained_image_;
    bool retained_valid_ = false;
    FullRepaintReason retained_reason_ = FullRepaintReason::first_frame;
    bool reload_pending_ = false;
    DamageTracker tracker_;
    FrameStats stats_{};
    bool gpu_stats_supported_ = false;
    std::shared_ptr<GpuTimer> gpu_timer_;
    std::shared_ptr<std::atomic_bool> device_lost_ = std::make_shared<std::atomic_bool>(false);
    detail::SkiaResourceCaches caches_;
    std::size_t layer_count_ = 0;
    gpu_runtime::SwapchainResizeState resize_state_;
    bool frame_configured_ = false;
    bool trim_when_settled_ = false;
    std::uint64_t trim_since_frame_ = 0;
    bool internal_redraw_ = false;
    bool device_loss_trimmed_ = false;
    std::vector<LoweringDiagnostic> diagnostics_;
    std::uint64_t trims_ = 0;
    CacheTrimReason last_trim_ = CacheTrimReason::none;
};

SkiaDawnSurface::SkiaDawnSurface(std::unique_ptr<Impl> impl) noexcept
    : impl_(std::move(impl)) {}

SkiaDawnSurface::~SkiaDawnSurface() = default;
SkiaDawnSurface::SkiaDawnSurface(SkiaDawnSurface&&) noexcept = default;
SkiaDawnSurface& SkiaDawnSurface::operator=(SkiaDawnSurface&&) noexcept = default;

std::unique_ptr<SkiaDawnSurface> SkiaDawnSurface::create(
    const Config& config, std::string* error) {
    auto implementation = std::make_unique<Impl>();
    if (!implementation->initialize(config, error)) {
        return nullptr;
    }
    return std::unique_ptr<SkiaDawnSurface>(
        new SkiaDawnSurface(std::move(implementation)));
}

bool SkiaDawnSurface::measure_text(
    const std::vector<TextRun>& runs,
    TextMetrics& metrics,
    std::string_view requested_font_directory,
    std::string* error) {
    std::unique_ptr<skia::textlayout::Paragraph> paragraph;
    return build_text_paragraph(
        runs, requested_font_directory, paragraph, metrics, error);
}

bool SkiaDawnSurface::render(const Scene& scene, std::string* error) {
    return impl_->render(scene, error);
}

bool SkiaDawnSurface::render_incremental(const Scene& scene, std::string* error) {
    return impl_->render_incremental(scene, error);
}

bool SkiaDawnSurface::render(
    const Scene& scene, const DamageRegion& damage, std::string* error) {
    return impl_->render(scene, damage, error);
}

void SkiaDawnSurface::invalidate(InvalidationReason reason, std::string_view node_id) {
    impl_->invalidate(reason, node_id);
}

FrameStats SkiaDawnSurface::frame_stats() const {
    return impl_->frame_stats();
}

void SkiaDawnSurface::set_cache_budgets(const RenderCacheBudgets& budgets) {
    impl_->set_cache_budgets(budgets);
}

RenderCacheBudgets SkiaDawnSurface::cache_budgets() const {
    return impl_->cache_budgets();
}

RenderTelemetry SkiaDawnSurface::telemetry() const {
    return impl_->telemetry();
}

bool SkiaDawnSurface::finish(std::string* error) {
    return impl_->finish(error);
}

void SkiaDawnSurface::simulate_device_loss() {
    impl_->simulate_device_loss();
}

bool SkiaDawnSurface::resize(
    std::uint32_t width, std::uint32_t height, float scale, std::string* error) {
    return impl_->resize(width, height, scale, error);
}

bool SkiaDawnSurface::capture_png(
    std::vector<std::uint8_t>& png, std::string* error) {
    return impl_->capture_png(png, error);
}

bool SkiaDawnSurface::capture_rgba(
    std::vector<std::uint8_t>& rgba,
    std::uint32_t& pixel_width,
    std::uint32_t& pixel_height,
    std::string* error) {
    return impl_->capture_rgba(rgba, pixel_width, pixel_height, error);
}

const GpuEvidence& SkiaDawnSurface::evidence() const noexcept {
    return impl_->evidence();
}

const std::vector<LoweringDiagnostic>& SkiaDawnSurface::diagnostics() const noexcept {
    return impl_->diagnostics();
}

}  // namespace vellum::graphics
