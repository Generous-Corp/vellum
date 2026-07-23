#include <vellum/graphics/skia_dawn_surface.hpp>

#import <QuartzCore/CAMetalLayer.h>

#include "dawn/dawn_proc.h"
#include "dawn/native/DawnNative.h"
#include "include/core/SkCanvas.h"
#include "include/core/SkColorSpace.h"
#include "include/core/SkData.h"
#include "include/core/SkFont.h"
#include "include/core/SkFontMgr.h"
#include "include/core/SkTypeface.h"
#include "include/core/SkImageInfo.h"
#include "include/core/SkImage.h"
#include "include/core/SkPaint.h"
#include "include/core/SkPixmap.h"
#include "include/core/SkRRect.h"
#include "include/core/SkStream.h"
#include "include/core/SkSurface.h"
#include "include/encode/SkPngEncoder.h"
#include "include/gpu/graphite/BackendTexture.h"
#include "include/gpu/graphite/Context.h"
#include "include/gpu/graphite/ContextOptions.h"
#include "include/gpu/graphite/Recorder.h"
#include "include/gpu/graphite/Recording.h"
#include "include/gpu/graphite/Surface.h"
#include "include/gpu/graphite/dawn/DawnBackendContext.h"
#include "include/gpu/graphite/dawn/DawnGraphiteTypes.h"
#include "include/ports/SkFontMgr_mac_ct.h"
#include "webgpu/webgpu_cpp.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstring>
#include <optional>
#include <thread>
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

void paint_node(SkCanvas& canvas, const SceneNode& node, float parent_x, float parent_y) {
    const float x = parent_x + node.bounds.x;
    const float y = parent_y + node.bounds.y;
    SkPaint paint;
    paint.setAntiAlias(true);
    paint.setColor4f(sk_color(node.fill));

    if (node.kind == SceneNode::Kind::rectangle) {
        const auto rect = SkRect::MakeXYWH(x, y, node.bounds.width, node.bounds.height);
        if (node.corner_radius > 0.0F) {
            canvas.drawRoundRect(rect, node.corner_radius, node.corner_radius, paint);
        } else {
            canvas.drawRect(rect, paint);
        }
    } else if (node.kind == SceneNode::Kind::text && !node.text.empty()) {
        static const sk_sp<SkFontMgr> font_manager = SkFontMgr_New_CoreText(nullptr);
        const auto typeface = font_manager
            ? font_manager->matchFamilyStyle("Helvetica Neue", SkFontStyle::Normal())
            : nullptr;
        SkFont font(typeface, std::max(node.font_size, 1.0F));
        font.setEdging(SkFont::Edging::kAntiAlias);
        canvas.drawString(node.text.c_str(), x, y + node.font_size, font, paint);
    }

    for (const auto& child : node.children) {
        paint_node(canvas, child, x, y);
    }
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
        paint_node(*canvas, scene.root, 0.0F, 0.0F);
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
