#include <vellum/graphics/skia_dawn_surface.hpp>
#include <vellum/graphics/paint_command.hpp>

#import <QuartzCore/CAMetalLayer.h>

#include "dawn/dawn_proc.h"
#include "dawn/native/DawnNative.h"
#include "include/core/SkCanvas.h"
#include "include/core/SkBlurTypes.h"
#include "include/core/SkColorSpace.h"
#include "include/core/SkData.h"
#include "include/core/SkFontMgr.h"
#include "include/core/SkMaskFilter.h"
#include "include/core/SkTypeface.h"
#include "include/core/SkImageInfo.h"
#include "include/core/SkImage.h"
#include "include/core/SkPaint.h"
#include "include/core/SkPixmap.h"
#include "include/core/SkRRect.h"
#include "include/core/SkStream.h"
#include "include/core/SkSurface.h"
#include "include/encode/SkPngEncoder.h"
#include "include/effects/SkGradient.h"
#include "include/gpu/graphite/BackendTexture.h"
#include "include/gpu/graphite/Context.h"
#include "include/gpu/graphite/ContextOptions.h"
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
            VELLUM_BUILD_DATADIR_FROM_GPU_DIR / "vellum" / "fonts";
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

struct DecodedUtf8 {
    std::uint32_t code_point = 0xFFFDU;
    std::size_t end = 0U;
    bool valid = false;
};

DecodedUtf8 decode_utf8(std::string_view text, std::size_t start) {
    const auto lead = static_cast<unsigned char>(text[start]);
    if (lead < 0x80U) return {lead, start + 1U, true};
    int length = 0;
    std::uint32_t value = 0U;
    std::uint32_t minimum = 0U;
    if ((lead & 0xE0U) == 0xC0U) {
        length = 2; value = lead & 0x1FU; minimum = 0x80U;
    } else if ((lead & 0xF0U) == 0xE0U) {
        length = 3; value = lead & 0x0FU; minimum = 0x800U;
    } else if ((lead & 0xF8U) == 0xF0U) {
        length = 4; value = lead & 0x07U; minimum = 0x10000U;
    } else {
        return {0xFFFDU, start + 1U, false};
    }
    if (start + static_cast<std::size_t>(length) > text.size()) {
        return {0xFFFDU, start + 1U, false};
    }
    for (int index = 1; index < length; ++index) {
        const auto byte = static_cast<unsigned char>(text[start + static_cast<std::size_t>(index)]);
        if ((byte & 0xC0U) != 0x80U) return {0xFFFDU, start + 1U, false};
        value = (value << 6U) | (byte & 0x3FU);
    }
    if (value < minimum || value > 0x10FFFFU ||
        (value >= 0xD800U && value <= 0xDFFFU)) {
        return {0xFFFDU, start + 1U, false};
    }
    return {value, start + static_cast<std::size_t>(length), true};
}

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
    constexpr std::string_view replacement = "?";
    std::string result;
    result.reserve(text.size());
    for (std::size_t offset = 0U; offset < text.size();) {
        const auto decoded = decode_utf8(text, offset);
        if (decoded.valid && has_packaged_glyph(decoded.code_point, typefaces)) {
            result.append(text.substr(offset, decoded.end - offset));
        } else {
            result.append(replacement);
        }
        offset = decoded.end;
    }
    return result;
}

bool draw_linear_gradient(
    SkCanvas& canvas, const PaintCommand& command,
    const ResolvedLinearGradient& gradient, std::string* error) {
    if (command.bounds.width <= 0.0F || command.bounds.height <= 0.0F) return true;
    if (gradient.stops.size() < 2U) {
        set_error(error, "Vellum linear gradient needs at least two stops");
        return false;
    }
    const float vector_x = gradient.end_x - gradient.start_x;
    const float vector_y = gradient.end_y - gradient.start_y;
    const float length = std::hypot(vector_x, vector_y);
    if (length <= 0.0F) {
        set_error(error, "Vellum linear gradient has a zero-length paint box");
        return false;
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
    std::vector<SkColor4f> colors;
    std::vector<float> positions;
    colors.reserve(gradient.stops.size());
    positions.reserve(gradient.stops.size());
    for (const auto& stop : gradient.stops) {
        colors.push_back(sk_color(stop.color));
        positions.push_back(stop.position);
    }
    const SkGradient shader_gradient{
        SkGradient::Colors(
            colors, positions,
            gradient.repeating ? SkTileMode::kRepeat : SkTileMode::kClamp),
        {},
    };
    auto shader = SkShaders::LinearGradient(points, shader_gradient);
    if (!shader) {
        set_error(error, "Vellum could not create the linear-gradient shader");
        return false;
    }

    canvas.save();
    const auto rect = SkRect::MakeXYWH(
        command.bounds.x, command.bounds.y,
        command.bounds.width, command.bounds.height);
    if (command.corner_radius > 0.0F) {
        canvas.clipRRect(
            SkRRect::MakeRectXY(rect, command.corner_radius, command.corner_radius),
            true);
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

bool draw_rectangle(SkCanvas& canvas, const PaintCommand& command, std::string* error) {
    const auto rect = SkRect::MakeXYWH(
        command.bounds.x, command.bounds.y,
        command.bounds.width, command.bounds.height);
    for (const auto& shadow : command.box_shadows) {
        SkPaint shadow_paint;
        shadow_paint.setAntiAlias(true);
        shadow_paint.setColor4f(sk_color(shadow.color));
        if (shadow.blur_radius > 0.0F) {
            shadow_paint.setMaskFilter(SkMaskFilter::MakeBlur(
                kNormal_SkBlurStyle, shadow.blur_radius * 0.5F, true));
        }
        const auto shadow_rect = SkRect::MakeXYWH(
            command.bounds.x + shadow.offset_x - shadow.spread_radius,
            command.bounds.y + shadow.offset_y - shadow.spread_radius,
            command.bounds.width + shadow.spread_radius * 2.0F,
            command.bounds.height + shadow.spread_radius * 2.0F);
        const float radius = std::max(0.0F, command.corner_radius + shadow.spread_radius);
        canvas.save();
        if (command.corner_radius > 0.0F) {
            canvas.clipRRect(
                SkRRect::MakeRectXY(
                    rect, command.corner_radius, command.corner_radius),
                SkClipOp::kDifference, true);
        } else {
            canvas.clipRect(rect, SkClipOp::kDifference, true);
        }
        canvas.drawRoundRect(shadow_rect, radius, radius, shadow_paint);
        canvas.restore();
    }

    SkPaint paint;
    paint.setAntiAlias(true);
    paint.setColor4f(sk_color(command.fill));
    if (command.corner_radius > 0.0F) {
        canvas.drawRoundRect(rect, command.corner_radius, command.corner_radius, paint);
    } else {
        canvas.drawRect(rect, paint);
    }
    for (const auto& declared : command.fill_gradients) {
        const auto gradient = resolve_linear_gradient(command.bounds, declared);
        if (!draw_linear_gradient(canvas, command, gradient, error)) return false;
    }
    return true;
}

bool draw_text(SkCanvas& canvas, const PaintCommand& command,
               std::string_view directory, std::string* error) {
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
    std::unique_ptr<skia::textlayout::Paragraph> paragraph;
    TextMetrics metrics;
    if (!build_text_paragraph(runs, directory, paragraph, metrics, error)) {
        return false;
    }
    paragraph->paint(&canvas, command.bounds.x, command.bounds.y);
    return true;
}

bool paint_command(SkCanvas& canvas, const PaintCommand& command,
                   std::string_view directory, std::string* error) {
    if (command.kind == PaintCommand::Kind::rectangle) {
        return draw_rectangle(canvas, command, error);
    }
    if (command.kind == PaintCommand::Kind::text &&
        (!command.text.empty() || !command.text_runs.empty())) {
        return draw_text(canvas, command, directory, error);
    }
    return true;
}

}  // namespace

class SkiaDawnSurface::Impl final {
public:
    ~Impl() {
        frame_surface_.reset();
        offscreen_surface_.reset();
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

        const DawnProcTable& procedures = dawn::native::GetProcs();
        dawnProcSetProcs(&procedures);

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
        if (!surface_ && !create_offscreen(error)) {
            return false;
        }

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
        if (surface_) {
            return configure_surface(error);
        }
        return create_offscreen(error);
    }

    bool render(const Scene& scene, std::string* error) {
        if (!render_internal(scene, false, nullptr, error)) {
            return false;
        }
        last_scene_ = scene;
        return true;
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
        if (!render_internal(*last_scene_, true, &rgba, error)) {
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

    bool create_offscreen(std::string* error) {
        const auto image_info = SkImageInfo::MakeN32Premul(
            physical_width(), physical_height());
        offscreen_surface_ = SkSurfaces::RenderTarget(recorder_.get(), image_info);
        if (!offscreen_surface_) {
            set_error(error, "Skia Graphite could not allocate an offscreen GPU surface");
            return false;
        }
        return true;
    }

    bool render_internal(const Scene& scene, bool readback,
                         std::vector<std::uint8_t>* rgba, std::string* error) {
        if (!context_ || !recorder_ || !evidence_.available) {
            set_error(error, "Skia/Dawn surface is not initialized");
            return false;
        }

        frame_surface_.reset();
        SkSurface* target = nullptr;
        if (surface_) {
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
            target = frame_surface_.get();
        } else {
            target = offscreen_surface_.get();
        }
        if (target == nullptr) {
            set_error(error, "no GPU render target is available");
            return false;
        }

        auto* canvas = target->getCanvas();
        canvas->save();
        canvas->resetMatrix();
        canvas->clear(sk_color(scene.background));
        canvas->scale(config_.scale, config_.scale);
        for (const auto& command : make_paint_commands(scene)) {
            if (!paint_command(*canvas, command, config_.font_directory, error)) {
                canvas->restore();
                current_texture_ = nullptr;
                return false;
            }
        }
        canvas->restore();

        auto recording = recorder_->snap();
        if (!recording) {
            set_error(error, "Skia Graphite produced no recording");
            current_texture_ = nullptr;
            return false;
        }
        skgpu::graphite::InsertRecordingInfo insertion{};
        insertion.fRecording = recording.get();
        context_->insertRecording(insertion);
        context_->submit(skgpu::graphite::SyncToCpu::kNo);

        if (readback && rgba != nullptr) {
            if (!read_target_rgba(*target, *rgba, error)) {
                current_texture_ = nullptr;
                return false;
            }
        }

        if (surface_) {
            surface_.Present();
            current_texture_ = nullptr;
        }
        instance_.ProcessEvents();
        return true;
    }

    bool read_target_rgba(
        SkSurface& target, std::vector<std::uint8_t>& rgba, std::string* error) {
        const auto image_info = SkImageInfo::Make(
            physical_width(), physical_height(), kRGBA_8888_SkColorType,
            kPremul_SkAlphaType, SkColorSpace::MakeSRGB());
        const auto row_bytes = static_cast<std::size_t>(physical_width()) * 4U;

        struct ReadbackState final {
            std::size_t row_bytes = 0;
            std::uint32_t height = 0;
            std::vector<std::uint8_t> pixels;
            std::atomic_bool finished{false};
            bool ok = false;
        };
        auto state = std::make_shared<ReadbackState>();
        state->row_bytes = row_bytes;
        state->height = static_cast<std::uint32_t>(physical_height());

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
            state->pixels.resize(
                static_cast<std::size_t>(state->height) * state->row_bytes);
            for (std::uint32_t y = 0; y < state->height; ++y) {
                std::memcpy(
                    state->pixels.data() + static_cast<std::size_t>(y) * state->row_bytes,
                    source + static_cast<std::size_t>(y) * result->rowBytes(0),
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
    sk_sp<SkSurface> offscreen_surface_;
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

}  // namespace vellum::graphics
