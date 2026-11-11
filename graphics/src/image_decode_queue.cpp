#include <vellum/graphics/image_decode_queue.hpp>

#include <algorithm>
#include <condition_variable>
#include <deque>
#include <exception>
#include <mutex>
#include <thread>
#include <unordered_map>
#include <unordered_set>
#include <utility>

namespace vellum::graphics {
namespace {

struct Job final {
    enum class Phase : std::uint8_t { queued, running, finished };

    std::string hash;
    std::vector<std::uint8_t> encoded;
    std::size_t estimate = 0;
    Phase phase = Phase::queued;
    bool cancelled = false;
    std::shared_ptr<const ImageAsset> asset;
    std::string error;
};

void set_error(std::string* error, std::string message) {
    if (error != nullptr) *error = std::move(message);
}

void collect_hashes(const SceneNode& node, std::unordered_set<std::string_view>& hashes) {
    if (node.kind == SceneNode::Kind::image && node.image) hashes.insert(node.image->content_hash);
    for (const auto& child : node.children) collect_hashes(child, hashes);
}

void collect_pending(const SceneNode& node, const std::unordered_set<std::string_view>& held,
                     const ImageDecodeQueue& queue, std::vector<PendingImage>& output) {
    if (node.kind == SceneNode::Kind::image && node.image &&
        !held.contains(node.image->content_hash) && queue.is_pending(node.image->content_hash)) {
        output.push_back({node.id, node.image->content_hash});
    }
    for (const auto& child : node.children) collect_pending(child, held, queue, output);
}

}  // namespace

struct ImageDecodeQueue::State final {
    mutable std::mutex mutex;
    std::condition_variable work;
    std::condition_variable idle;
    ImageDecodeFunction decode;
    ImageDecodeBudget budget;
    std::unordered_map<std::string, std::shared_ptr<Job>> jobs;
    std::deque<std::shared_ptr<Job>> queue;
    std::vector<std::shared_ptr<Job>> finished;
    std::size_t running = 0;
    std::size_t inflight = 0;
    bool stopping = false;
    ImageDecodeStats stats;
    std::vector<std::thread> workers;

    bool startable() const {
        if (queue.empty() || running >= budget.max_concurrent) return false;
        return running == 0 || inflight + queue.front()->estimate <= budget.max_inflight_bytes;
    }

    bool cancel_locked(const std::string& hash) {
        const auto found = jobs.find(hash);
        if (found == jobs.end()) return false;
        const auto job = found->second;
        jobs.erase(found);
        job->cancelled = true;
        ++stats.cancelled;
        if (job->phase == Job::Phase::queued) {
            queue.erase(std::find(queue.begin(), queue.end(), job));
        } else if (job->phase == Job::Phase::finished) {
            finished.erase(std::find(finished.begin(), finished.end(), job));
        }
        idle.notify_all();
        work.notify_all();
        return true;
    }

    void run() {
        std::unique_lock lock(mutex);
        for (;;) {
            work.wait(lock, [this] { return stopping || startable(); });
            if (stopping) return;
            const auto job = queue.front();
            queue.pop_front();
            job->phase = Job::Phase::running;
            ++running;
            inflight += job->estimate;
            stats.peak_running = std::max(stats.peak_running, running);
            stats.peak_inflight_bytes = std::max(stats.peak_inflight_bytes, inflight);
            lock.unlock();

            std::string error;
            std::shared_ptr<const ImageAsset> asset;
            try {
                asset = decode(job->encoded, job->hash, &error);
                if (!asset && error.empty()) error = "the image could not be decoded";
            } catch (const std::exception& exception) {
                asset = nullptr;
                error = std::string("the decoder threw: ") + exception.what();
            } catch (...) {
                asset = nullptr;
                error = "the decoder threw";
            }

            lock.lock();
            --running;
            inflight -= job->estimate;
            std::vector<std::uint8_t>().swap(job->encoded);
            if (!job->cancelled) {
                job->phase = Job::Phase::finished;
                job->asset = std::move(asset);
                job->error = std::move(error);
                ++(job->asset ? stats.completed : stats.failed);
                finished.push_back(job);
            }
            idle.notify_all();
            work.notify_all();
        }
    }
};

ImageDecodeQueue::ImageDecodeQueue(ImageDecodeFunction decode, ImageDecodeBudget budget)
    : state_(std::make_unique<State>()) {
    budget.max_concurrent = std::max<std::size_t>(budget.max_concurrent, 1U);
    state_->decode = std::move(decode);
    state_->budget = budget;
    state_->workers.reserve(budget.max_concurrent);
    for (std::size_t index = 0; index < budget.max_concurrent; ++index) {
        state_->workers.emplace_back([state = state_.get()] { state->run(); });
    }
}

ImageDecodeQueue::~ImageDecodeQueue() {
    {
        const std::lock_guard lock(state_->mutex);
        state_->stopping = true;
        state_->queue.clear();
        state_->jobs.clear();
        state_->finished.clear();
    }
    state_->work.notify_all();
    for (auto& worker : state_->workers) worker.join();
}

ImageDecodeQueue::Submit ImageDecodeQueue::submit(
    std::vector<std::uint8_t> encoded, std::string_view content_hash, std::string* error) {
    // Everything that bounds the work is checked before the bytes are queued.
    const auto hash = normalize_content_hash(content_hash);
    if (!hash) {
        set_error(error, "the content hash is not a SHA-256 hash");
        return Submit::refused;
    }
    if (encoded.size() > kMaxEncodedImageBytes) {
        set_error(error, "the encoded image is larger than " +
                             std::to_string(kMaxEncodedImageBytes) + " bytes");
        return Submit::refused;
    }
    const auto declared = png_header_dimensions(encoded);
    if (!declared) {
        set_error(error, "the image is not a PNG that starts with an IHDR header");
        return Submit::refused;
    }
    if (!image_dimensions_fit(declared->width, declared->height)) {
        set_error(error, "the PNG size is empty or over the image budget");
        return Submit::refused;
    }
    const std::lock_guard lock(state_->mutex);
    if (state_->stopping) {
        set_error(error, "the decode queue is shutting down");
        return Submit::refused;
    }
    if (state_->jobs.contains(*hash)) return Submit::already_pending;
    auto job = std::make_shared<Job>();
    job->hash = *hash;
    job->encoded = std::move(encoded);
    job->estimate = static_cast<std::size_t>(declared->width) * declared->height * 4U;
    state_->jobs.emplace(*hash, job);
    state_->queue.push_back(std::move(job));
    ++state_->stats.submitted;
    state_->work.notify_one();
    return Submit::queued;
}

bool ImageDecodeQueue::cancel(std::string_view content_hash) {
    const std::lock_guard lock(state_->mutex);
    return state_->cancel_locked(std::string(content_hash));
}

std::size_t ImageDecodeQueue::cancel_unreferenced(const Scene& scene) {
    std::unordered_set<std::string_view> referenced;
    collect_hashes(scene.root, referenced);
    const std::lock_guard lock(state_->mutex);
    std::vector<std::string> unreferenced;
    for (const auto& entry : state_->jobs) {
        if (!referenced.contains(entry.first)) unreferenced.push_back(entry.first);
    }
    for (const auto& hash : unreferenced) state_->cancel_locked(hash);
    return unreferenced.size();
}

bool ImageDecodeQueue::is_pending(std::string_view content_hash) const {
    const std::lock_guard lock(state_->mutex);
    return state_->jobs.contains(std::string(content_hash));
}

std::vector<DecodedImage> ImageDecodeQueue::take_finished() {
    const std::lock_guard lock(state_->mutex);
    std::vector<DecodedImage> output;
    output.reserve(state_->finished.size());
    for (auto& job : state_->finished) {
        state_->jobs.erase(job->hash);
        output.push_back({job->hash, std::move(job->asset), std::move(job->error)});
    }
    state_->finished.clear();
    return output;
}

bool ImageDecodeQueue::wait_idle(std::chrono::milliseconds timeout) {
    std::unique_lock lock(state_->mutex);
    const auto idle = [this] { return state_->queue.empty() && state_->running == 0; };
    if (timeout == std::chrono::milliseconds::max()) {
        state_->idle.wait(lock, idle);
        return true;
    }
    return state_->idle.wait_for(lock, timeout, idle);
}

ImageDecodeStats ImageDecodeQueue::stats() const {
    const std::lock_guard lock(state_->mutex);
    auto output = state_->stats;
    output.queued = state_->queue.size();
    output.running = state_->running;
    output.finished = state_->finished.size();
    output.inflight_bytes = state_->inflight;
    return output;
}

std::vector<PendingImage> pending_images(const Scene& scene, const ImageDecodeQueue& queue) {
    std::unordered_set<std::string_view> held;
    for (const auto& asset : scene.images) {
        if (asset) held.insert(asset->content_hash);
    }
    std::vector<PendingImage> output;
    collect_pending(scene.root, held, queue, output);
    return output;
}

PublishedImages publish_decoded_images(ImageDecodeQueue& queue, Scene& scene) {
    std::unordered_set<std::string_view> referenced;
    collect_hashes(scene.root, referenced);
    PublishedImages output;
    for (auto& decoded : queue.take_finished()) {
        if (!referenced.contains(decoded.content_hash)) {
            ++output.discarded;
        } else if (!decoded.asset) {
            output.failed.push_back(std::move(decoded));
        } else if (find_image_asset(scene, decoded.content_hash)) {
            ++output.discarded;
        } else {
            scene.images.push_back(std::move(decoded.asset));
            ++output.published;
        }
    }
    return output;
}

}  // namespace vellum::graphics
