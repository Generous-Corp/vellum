#pragma once

#include <vellum/graphics/image_asset.hpp>
#include <vellum/graphics/scene.hpp>

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <memory>
#include <span>
#include <string>
#include <string_view>
#include <vector>

namespace vellum::graphics {

/// Turns encoded bytes into an asset, off the calling thread. It receives the
/// bytes and the normalized content hash they must have, and returns null with
/// a reason in `error` on failure. `SkiaDawnSurface::decode_image_asset` has
/// this shape. It may run on any worker thread and runs concurrently with
/// itself, so it must not touch a frame, a canvas or the Dawn device.
using ImageDecodeFunction = std::function<std::shared_ptr<const ImageAsset>(
    std::span<const std::uint8_t> encoded, std::string_view content_hash, std::string* error)>;

/// What a host lets decoding use at once.
struct ImageDecodeBudget final {
    /// The most decodes in progress at the same time, and the number of
    /// worker threads. At least one.
    std::size_t max_concurrent = 2;
    /// The most decoded texel bytes (width by height by four, read from the
    /// PNG header) across decodes in progress. A decode that would pass it
    /// waits in the queue; one that is alone in progress always runs, so a
    /// single image over the budget is delayed, never starved.
    std::size_t max_inflight_bytes = 512U * 1024U * 1024U;
};

struct ImageDecodeStats final {
    std::size_t queued = 0;
    std::size_t running = 0;
    /// Finished decodes (published or failed) not yet taken.
    std::size_t finished = 0;
    /// Decoded texel bytes of the decodes in progress.
    std::size_t inflight_bytes = 0;
    std::size_t peak_running = 0;
    std::size_t peak_inflight_bytes = 0;
    std::uint64_t submitted = 0;
    std::uint64_t completed = 0;
    std::uint64_t failed = 0;
    std::uint64_t cancelled = 0;
};

/// A finished decode: the asset, or null with the reason it failed.
struct DecodedImage final {
    std::string content_hash;
    std::shared_ptr<const ImageAsset> asset;
    std::string error;
};

/// A host-owned decoder: PNG decoding runs on its worker threads while the
/// render thread keeps drawing, within an `ImageDecodeBudget`.
///
/// Everything that bounds the work is checked on the calling thread before a
/// job is queued: the encoded size against `kMaxEncodedImageBytes`, the PNG
/// signature and IHDR, and the declared dimensions against the image budgets.
/// A refused input is never queued or copied. Hashing and decoding run on a
/// worker.
///
/// Jobs run in submission order. Over budget a job waits; it is never dropped.
/// Only the render thread publishes: `take_finished` hands the finished assets
/// over, and the host puts them in the scene (see `publish_decoded_images`) and
/// uploads them there. The scene diff then damages exactly the nodes whose
/// asset changed. Every member may be called from any thread.
///
/// Destruction discards queued jobs, waits for the decodes in progress and
/// drops their results.
class ImageDecodeQueue final {
public:
    enum class Submit : std::uint8_t {
        /// The job was queued.
        queued,
        /// A job for this hash is already queued, running or finished and not
        /// yet taken, so nothing was added.
        already_pending,
        /// The input was refused; `error` says why.
        refused,
    };

    explicit ImageDecodeQueue(ImageDecodeFunction decode, ImageDecodeBudget budget = {});
    ~ImageDecodeQueue();
    ImageDecodeQueue(const ImageDecodeQueue&) = delete;
    ImageDecodeQueue& operator=(const ImageDecodeQueue&) = delete;

    /// Queues `encoded` to decode as the asset named `content_hash`, which
    /// must be a SHA-256 hash (bare or prefixed). The decode function checks
    /// the bytes against it.
    Submit submit(std::vector<std::uint8_t> encoded, std::string_view content_hash,
                  std::string* error = nullptr);

    /// Drops the job for `content_hash`, queued, running or finished: its
    /// result is never handed out. A running decode is not interrupted; it
    /// finishes and its asset is discarded. Returns whether there was a job.
    bool cancel(std::string_view content_hash);

    /// Cancels every job whose hash no image node of `scene` names. Returns
    /// how many.
    std::size_t cancel_unreferenced(const Scene& scene);

    /// Whether a job for `content_hash` is queued, running or finished and not
    /// yet taken.
    [[nodiscard]] bool is_pending(std::string_view content_hash) const;

    /// Removes and returns the finished jobs, in the order they finished.
    [[nodiscard]] std::vector<DecodedImage> take_finished();

    /// Blocks until nothing is queued or running, or `timeout` passes. Returns
    /// whether the queue went idle. Finished results are still to be taken.
    bool wait_idle(std::chrono::milliseconds timeout = std::chrono::milliseconds::max());

    [[nodiscard]] ImageDecodeStats stats() const;

private:
    struct State;
    std::unique_ptr<State> state_;
};

/// An image node whose asset is still decoding.
struct PendingImage final {
    std::string node_id;
    std::string content_hash;
};

/// The image nodes of `scene` whose asset is not in `scene.images` and has a
/// job in `queue`, in depth-first order. Such a node draws
/// `kMissingImagePlaceholder` as it would for any absent asset; this names the
/// ones that will fill in, so a host can tell a pending frame from a finished
/// one. A capture is final when this is empty after `wait_idle` and
/// `publish_decoded_images`.
[[nodiscard]] std::vector<PendingImage> pending_images(
    const Scene& scene, const ImageDecodeQueue& queue);

/// What `publish_decoded_images` did with the finished jobs.
struct PublishedImages final {
    /// Assets added to `scene.images`.
    std::size_t published = 0;
    /// Results dropped because no image node names the hash any more or the
    /// scene already holds an asset under it.
    std::size_t discarded = 0;
    /// Decodes that failed; their nodes keep drawing the placeholder.
    std::vector<DecodedImage> failed;
};

/// Takes the finished jobs of `queue` and appends each asset a node of `scene`
/// still names to `scene.images`. Call it on the thread that owns the scene
/// before the next frame; the frame after it redraws exactly the nodes whose
/// asset arrived.
PublishedImages publish_decoded_images(ImageDecodeQueue& queue, Scene& scene);

}  // namespace vellum::graphics
