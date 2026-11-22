// Opt-in hardware qualification of Graphite in modern and older-SDK test hosts.
// This exercises shader compilation and pixel readback, not device startup
// alone.
#include "include/core/SkCanvas.h"
#include "include/core/SkImage.h"
#include "include/core/SkPaint.h"
#include "include/core/SkSurface.h"
#include "include/gpu/graphite/Context.h"
#include "include/gpu/graphite/ContextOptions.h"
#include "include/gpu/graphite/Recorder.h"
#include "include/gpu/graphite/Recording.h"
#include "include/gpu/graphite/Surface.h"
#include "include/gpu/graphite/dawn/DawnBackendContext.h"
#include "webgpu/webgpu_cpp.h"
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <gpu_runtime/dawn_native_bootstrap.hpp>
#include <gpu_runtime/gpu_surface.hpp>
#include <thread>

int main() {
  std::string bootstrap_error;
  if (!gpu_runtime::register_native_dawn_bootstrap(
          "org.vellum.graphite-host-probe", &bootstrap_error)) {
    std::fprintf(stderr, "bootstrap failed: %s\n", bootstrap_error.c_str());
    return 1;
  }
  auto gpu = gpu_runtime::GpuSurface::create_dawn();
  if (!gpu || !gpu->initialize({.width = 64, .height = 64}))
    return 2;
  auto adapter = gpu->adapter_info();
  std::printf("adapter=%s backend=%s\n", adapter.name.c_str(),
              adapter.backend_type.c_str());
  if (!adapter.available || adapter.null_backend ||
      adapter.backend_type != "Metal" ||
      adapter.adapter_type == gpu_runtime::GpuSurface::AdapterType::cpu)
    return 3;
  skgpu::graphite::DawnBackendContext backend;
  backend.fDevice = *static_cast<wgpu::Device *>(gpu->dawn_device_handle());
  backend.fQueue = *static_cast<wgpu::Queue *>(gpu->dawn_queue_handle());
  backend.fInstance =
      *static_cast<wgpu::Instance *>(gpu->dawn_instance_handle());
  skgpu::graphite::ContextOptions opts;
  opts.fInternalMultisampleCount = skgpu::graphite::SampleCount::k1;
  auto ctx = skgpu::graphite::ContextFactory::MakeDawn(backend, opts);
  if (!ctx)
    return 4;
  auto recorder = ctx->makeRecorder();
  if (!recorder)
    return 5;
  auto info =
      SkImageInfo::Make(64, 64, kRGBA_8888_SkColorType, kPremul_SkAlphaType);
  auto surface = SkSurfaces::RenderTarget(recorder.get(), info);
  if (!surface)
    return 5;
  surface->getCanvas()->clear(SK_ColorBLACK);
  SkPaint paint;
  paint.setColor(SK_ColorGREEN);
  paint.setAntiAlias(true);
  surface->getCanvas()->drawCircle(32, 32, 20, paint);
  auto recording = recorder->snap();
  if (!recording)
    return 6;
  skgpu::graphite::InsertRecordingInfo insert;
  insert.fRecording = recording.get();
  auto result = ctx->insertRecording(insert);
  std::printf("insert_status=%d\n",
              result == skgpu::graphite::InsertStatus::kSuccess ? 0 : 1);
  if (result != skgpu::graphite::InsertStatus::kSuccess)
    return 7;
  struct Readback {
    std::atomic<bool> done{false};
    bool ok = false;
    unsigned char center[4] = {};
    unsigned char corner[4] = {};
  } state;
  ctx->asyncRescaleAndReadPixels(
      surface.get(), info, SkIRect::MakeWH(64, 64), SkImage::RescaleGamma::kSrc,
      SkImage::RescaleMode::kNearest,
      [](void *opaque, std::unique_ptr<const SkImage::AsyncReadResult> result) {
        auto &s = *static_cast<Readback *>(opaque);
        if (result && result->count() == 1) {
          auto *data = static_cast<const unsigned char *>(result->data(0));
          std::memcpy(s.center, data + 32 * result->rowBytes(0) + 32 * 4, 4);
          std::memcpy(s.corner, data, 4);
          s.ok = true;
        }
        s.done.store(true, std::memory_order_release);
      },
      &state);
  ctx->submit(skgpu::graphite::SyncToCpu::kYes);
  auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);
  while (!state.done.load(std::memory_order_acquire) &&
         std::chrono::steady_clock::now() < deadline) {
    ctx->checkAsyncWorkCompletion();
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  // A late callback owns this stack address. On timeout, exit the isolated
  // test process instead of unwinding the context while it still holds it.
  if (!state.done.load() || !state.ok) {
    std::fprintf(stderr, "readback incomplete\n");
    std::fflush(nullptr);
    std::_Exit(8);
  }
  std::printf("center=%u,%u,%u,%u corner=%u,%u,%u,%u\n", state.center[0],
              state.center[1], state.center[2], state.center[3],
              state.corner[0], state.corner[1], state.corner[2],
              state.corner[3]);
  if (state.center[1] < 250 || state.center[0] > 5 || state.center[2] > 5 ||
      state.center[3] < 250 || state.corner[3] < 250 || state.corner[0] > 5 ||
      state.corner[1] > 5 || state.corner[2] > 5)
    return 9;
  puts("PASS: hardware Graphite draw and pixel readback");
  return 0;
}
