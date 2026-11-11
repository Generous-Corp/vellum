// The off-thread image decode queue: refusals before queueing, the budgets,
// cancellation, publishing into a scene, teardown with decodes in flight, and
// a stress run. The decoder is a stand-in the test controls.

#include <vellum/graphics/image_decode_queue.hpp>

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <iostream>
#include <memory>
#include <mutex>
#include <set>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

namespace {

using namespace vellum::graphics;
using namespace std::chrono_literals;

int failures = 0;

void expect(bool ok, const std::string& what, const std::string& detail = {}) {
    if (ok) return;
    ++failures;
    std::cerr << "FAIL: " << what;
    if (!detail.empty()) std::cerr << " (" << detail << ')';
    std::cerr << '\n';
}

std::string hash_of(unsigned index) {
    std::string digits = std::to_string(index);
    return "sha256:" + std::string(64 - digits.size(), '0') + digits;
}

std::vector<std::uint8_t> fake_png(std::uint32_t width, std::uint32_t height) {
    std::vector<std::uint8_t> bytes{0x89, 'P', 'N', 'G', '\r', '\n', 0x1A, '\n',
                                    0,    0,   0,   13,  'I',  'H',  'D',  'R'};
    for (const auto value : {width, height}) {
        for (int shift = 24; shift >= 0; shift -= 8) {
            bytes.push_back(static_cast<std::uint8_t>((value >> shift) & 0xFFU));
        }
    }
    bytes.resize(64, 0);
    return bytes;
}

std::shared_ptr<const ImageAsset> tiny_asset(const std::string& hash) {
    const std::vector<std::uint8_t> texels{255, 0, 0, 255};
    return make_image_asset(hash, 1, 1, texels, ImageAlpha::premultiplied);
}

// A decoder that can hold every call until the test opens the gate, and
// records how many ran at once.
struct Gate final {
    std::mutex mutex;
    std::condition_variable changed;
    bool open = true;
    int running = 0;
    int peak = 0;
    int calls = 0;
    std::set<std::string> seen;
    std::vector<std::weak_ptr<const ImageAsset>> made;
    std::set<std::string> failing;

    void close() {
        const std::lock_guard lock(mutex);
        open = false;
    }
    void release() {
        {
            const std::lock_guard lock(mutex);
            open = true;
        }
        changed.notify_all();
    }
    bool wait_for_running(int count) {
        std::unique_lock lock(mutex);
        return changed.wait_for(lock, 5s, [&] { return running >= count; });
    }
    ImageDecodeFunction decoder() {
        return [this](std::span<const std::uint8_t>, std::string_view hash, std::string* error) {
            std::unique_lock lock(mutex);
            ++calls;
            ++running;
            peak = std::max(peak, running);
            seen.emplace(hash);
            changed.notify_all();
            changed.wait(lock, [&] { return open; });
            --running;
            changed.notify_all();
            if (failing.contains(std::string(hash))) {
                if (error != nullptr) *error = "the stand-in decoder failed";
                return std::shared_ptr<const ImageAsset>{};
            }
            auto asset = tiny_asset(std::string(hash));
            made.push_back(asset);
            return asset;
        };
    }
};

Scene scene_naming(const std::vector<std::string>& hashes) {
    Scene scene;
    scene.width = 32.0F;
    scene.height = 32.0F;
    unsigned index = 0;
    for (const auto& hash : hashes) {
        scene.root.children.push_back({.id = "image" + std::to_string(index++),
                                       .kind = SceneNode::Kind::image,
                                       .bounds = {0, 0, 8, 8},
                                       .image = ImageReference{.content_hash = hash}});
    }
    return scene;
}

void refusals() {
    Gate gate;
    ImageDecodeQueue queue(gate.decoder());
    std::string error;
    const auto refused = [&](std::vector<std::uint8_t> bytes, const std::string& hash,
                             const std::string& what) {
        error.clear();
        expect(queue.submit(std::move(bytes), hash, &error) == ImageDecodeQueue::Submit::refused &&
                   !error.empty(),
               what, error);
    };
    refused(std::vector<std::uint8_t>(kMaxEncodedImageBytes + 1U, 0), hash_of(1),
            "input over the encoded cap is refused");
    refused(std::vector<std::uint8_t>(64, 0x42), hash_of(1), "bytes that are not a PNG are refused");
    refused(fake_png(8193, 4), hash_of(1), "a header over the dimension limit is refused");
    refused(fake_png(0, 4), hash_of(1), "an empty header is refused");
    refused(fake_png(65535, 65535), hash_of(1), "a header over the byte budget is refused");
    refused(fake_png(4, 4), "logo.png", "a hash that is not SHA-256 is refused");
    refused(fake_png(4, 4), {}, "an empty hash is refused");
    auto truncated = fake_png(4, 4);
    truncated.resize(12);
    refused(std::move(truncated), hash_of(1), "a truncated header is refused");
    expect(queue.wait_idle(1s) && gate.calls == 0 && queue.stats().submitted == 0U &&
               queue.stats().queued == 0U && !queue.is_pending(hash_of(1)),
           "a refused input is never queued and never reaches the decoder");
    expect(queue.submit(fake_png(8192, 8192), hash_of(2)) == ImageDecodeQueue::Submit::queued,
           "an image at the limits is accepted");
    expect(queue.wait_idle(5s), "the accepted image decodes");
    const auto bare = hash_of(3).substr(7);
    expect(queue.submit(fake_png(4, 4), bare) == ImageDecodeQueue::Submit::queued,
           "a bare hash is accepted");
    expect(queue.wait_idle(5s), "the bare-hash image decodes");
    expect(queue.is_pending(hash_of(3)), "a bare hash is queued under its prefixed form");
}

void decoding_does_not_block_the_caller() {
    Gate gate;
    gate.close();
    ImageDecodeQueue queue(gate.decoder());
    auto scene = scene_naming({hash_of(1), hash_of(2)});
    expect(queue.submit(fake_png(4, 4), hash_of(1)) == ImageDecodeQueue::Submit::queued,
           "the job is queued");
    expect(gate.wait_for_running(1), "the decoder is running on a worker");
    const auto start = std::chrono::steady_clock::now();
    expect(queue.submit(fake_png(4, 4), hash_of(1)) == ImageDecodeQueue::Submit::already_pending,
           "a second submit of the same hash adds nothing");
    const auto pending = pending_images(scene, queue);
    const auto stats = queue.stats();
    expect(std::chrono::steady_clock::now() - start < 1s,
           "the caller keeps going while a decode is held");
    expect(pending.size() == 1U && pending[0].node_id == "image0" &&
               pending[0].content_hash == hash_of(1),
           "the pending node is named; a node nobody queued is only missing");
    expect(stats.running == 1U && stats.queued == 0U, "the stats show one decode running");
    expect(!queue.wait_idle(50ms), "wait_idle times out while a decode is held");
    auto published = publish_decoded_images(queue, scene);
    expect(published.published == 0U && scene.images.empty(),
           "nothing is published before the decode finishes");

    gate.release();
    expect(queue.wait_idle(5s), "the queue goes idle once the decode finishes");
    expect(queue.is_pending(hash_of(1)) && pending_images(scene, queue).size() == 1U,
           "a finished decode still counts as pending until it is published");
    published = publish_decoded_images(queue, scene);
    expect(published.published == 1U && scene.images.size() == 1U &&
               scene.images[0]->content_hash == hash_of(1),
           "the finished asset is appended to the scene");
    expect(pending_images(scene, queue).empty() && !queue.is_pending(hash_of(1)),
           "nothing is pending after publishing");
    expect(publish_decoded_images(queue, scene).published == 0U, "a result is handed out once");
}

void concurrency_budget() {
    Gate gate;
    gate.close();
    ImageDecodeQueue queue(gate.decoder(), {.max_concurrent = 2});
    for (unsigned index = 0; index < 6; ++index) {
        queue.submit(fake_png(4, 4), hash_of(index));
    }
    expect(gate.wait_for_running(2), "two decodes start");
    std::this_thread::sleep_for(100ms);
    auto stats = queue.stats();
    expect(stats.running == 2U && stats.queued == 4U && gate.calls == 2,
           "the rest wait in the queue", "running " + std::to_string(stats.running));
    gate.release();
    expect(queue.wait_idle(5s), "the queue drains");
    auto scene = scene_naming({hash_of(0), hash_of(1), hash_of(2), hash_of(3), hash_of(4),
                               hash_of(5)});
    const auto published = publish_decoded_images(queue, scene);
    expect(published.published == 6U && scene.images.size() == 6U,
           "every queued image is decoded; none is dropped");
    expect(gate.peak <= 2 && queue.stats().peak_running <= 2U, "never more than two at once",
           std::to_string(gate.peak));
    expect(gate.seen.size() == 6U, "each image decoded exactly once");
    expect(gate.calls == 6, "no image is decoded twice");

    Gate ordered;
    ordered.close();
    ImageDecodeQueue single(ordered.decoder(), {.max_concurrent = 1});
    for (unsigned index = 0; index < 4; ++index) single.submit(fake_png(4, 4), hash_of(index));
    ordered.release();
    expect(single.wait_idle(5s), "the single worker drains");
    const auto done = single.take_finished();
    bool in_order = done.size() == 4U;
    for (unsigned index = 0; in_order && index < 4; ++index) {
        in_order = done[index].content_hash == hash_of(index);
    }
    expect(in_order, "jobs run in submission order");
}

void byte_budget() {
    Gate gate;
    gate.close();
    // Each 16 by 16 image is 1024 decoded bytes; the budget holds two.
    ImageDecodeQueue queue(gate.decoder(), {.max_concurrent = 4, .max_inflight_bytes = 2048});
    for (unsigned index = 0; index < 5; ++index) {
        queue.submit(fake_png(16, 16), hash_of(index));
    }
    expect(gate.wait_for_running(2), "two decodes fit the byte budget");
    std::this_thread::sleep_for(100ms);
    auto stats = queue.stats();
    expect(stats.running == 2U && stats.queued == 3U && stats.inflight_bytes == 2048U,
           "the byte budget holds the rest back although workers are free",
           std::to_string(stats.running) + "/" + std::to_string(stats.inflight_bytes));
    gate.release();
    expect(queue.wait_idle(5s), "the queue drains");
    stats = queue.stats();
    expect(stats.completed == 5U && stats.peak_inflight_bytes <= 2048U &&
               stats.inflight_bytes == 0U,
           "all five complete within the budget", std::to_string(stats.peak_inflight_bytes));

    // One image over the whole budget runs alone instead of starving.
    Gate lone;
    lone.close();
    ImageDecodeQueue small(lone.decoder(), {.max_concurrent = 3, .max_inflight_bytes = 100});
    small.submit(fake_png(64, 64), hash_of(1));
    small.submit(fake_png(64, 64), hash_of(2));
    expect(lone.wait_for_running(1), "an image over the whole budget still starts");
    std::this_thread::sleep_for(100ms);
    expect(small.stats().running == 1U && small.stats().queued == 1U,
           "a second one waits for it");
    lone.release();
    expect(small.wait_idle(5s) && small.stats().completed == 2U && lone.peak == 1,
           "images over the byte budget run one at a time");
}

void cancellation() {
    Gate gate;
    gate.close();
    ImageDecodeQueue queue(gate.decoder(), {.max_concurrent = 1});
    queue.submit(fake_png(4, 4), hash_of(1));
    queue.submit(fake_png(4, 4), hash_of(2));
    queue.submit(fake_png(4, 4), hash_of(3));
    expect(gate.wait_for_running(1), "the first decode runs");
    expect(queue.cancel(hash_of(2)) && !queue.cancel(hash_of(2)), "a queued job is cancelled once");
    auto scene = scene_naming({hash_of(1)});
    expect(queue.cancel_unreferenced(scene) == 1U && !queue.is_pending(hash_of(3)),
           "a job no node names is cancelled");
    expect(queue.cancel(hash_of(1)) && !queue.is_pending(hash_of(1)),
           "a running job is cancelled without waiting for it");
    gate.release();
    expect(queue.wait_idle(5s), "the queue drains");
    expect(gate.calls == 1 && gate.seen.count(hash_of(2)) == 0U && gate.seen.count(hash_of(3)) == 0U,
           "a cancelled queued job never runs the decoder");
    scene = scene_naming({hash_of(1), hash_of(2), hash_of(3)});
    const auto published = publish_decoded_images(queue, scene);
    expect(published.published == 0U && scene.images.empty() && queue.take_finished().empty(),
           "a cancelled decode never publishes an asset");

    // A decode that finished but was not taken yet is cancelled too.
    queue.submit(fake_png(4, 4), hash_of(4));
    expect(queue.wait_idle(5s) && queue.is_pending(hash_of(4)), "the fourth decode finished");
    expect(queue.cancel(hash_of(4)) && queue.take_finished().empty(),
           "a finished result is dropped by cancel");

    // The same hash can be queued again after a cancel.
    gate.close();
    queue.submit(fake_png(4, 4), hash_of(5));
    expect(gate.wait_for_running(1), "the fifth decode runs");
    queue.cancel(hash_of(5));
    expect(queue.submit(fake_png(4, 4), hash_of(5)) == ImageDecodeQueue::Submit::queued,
           "a cancelled hash can be submitted again");
    gate.release();
    expect(queue.wait_idle(5s), "both decodes of the hash finish");
    const auto taken = queue.take_finished();
    expect(taken.size() == 1U && taken[0].content_hash == hash_of(5),
           "only the new job's result is handed out");
}

void publishing_rules() {
    Gate gate;
    gate.failing.insert(hash_of(2));
    ImageDecodeQueue queue(gate.decoder());
    queue.submit(fake_png(4, 4), hash_of(1));
    queue.submit(fake_png(4, 4), hash_of(2));
    queue.submit(fake_png(4, 4), hash_of(3));
    queue.submit(fake_png(4, 4), hash_of(4));
    expect(queue.wait_idle(5s), "the queue drains");
    auto scene = scene_naming({hash_of(1), hash_of(2), hash_of(4)});
    scene.images = {tiny_asset(hash_of(4))};
    const auto existing = scene.images[0];
    const auto published = publish_decoded_images(queue, scene);
    expect(published.published == 1U && scene.images.size() == 2U &&
               scene.images[1]->content_hash == hash_of(1),
           "a referenced asset is appended");
    expect(published.failed.size() == 1U && published.failed[0].content_hash == hash_of(2) &&
               published.failed[0].error == "the stand-in decoder failed",
           "a failed decode is reported with its reason");
    expect(published.discarded == 2U && scene.images[0] == existing,
           "an unreferenced result and a duplicate of a held asset are discarded");
    expect(queue.stats().failed == 1U && queue.stats().completed == 3U, "the stats count them");
    expect(pending_images(scene, queue).empty(), "a failed node is not pending");

    ImageDecodeQueue throwing([](std::span<const std::uint8_t>, std::string_view, std::string*)
                                  -> std::shared_ptr<const ImageAsset> { throw 42; });
    throwing.submit(fake_png(4, 4), hash_of(1));
    expect(throwing.wait_idle(5s), "a throwing decoder does not take the worker down");
    const auto failed = throwing.take_finished();
    expect(failed.size() == 1U && !failed[0].asset && !failed[0].error.empty(),
           "a throwing decoder is a failed decode");
}

void teardown_with_decodes_in_flight() {
    Gate gate;
    gate.close();
    std::thread opener;
    {
        ImageDecodeQueue queue(gate.decoder(), {.max_concurrent = 2});
        for (unsigned index = 0; index < 8; ++index) {
            queue.submit(fake_png(4, 4), hash_of(index));
        }
        expect(gate.wait_for_running(2), "two decodes are in flight");
        opener = std::thread([&] {
            std::this_thread::sleep_for(100ms);
            gate.release();
        });
    }
    opener.join();
    expect(gate.calls == 2, "queued jobs that never started are discarded at teardown",
           std::to_string(gate.calls));
    bool leaked = false;
    for (const auto& weak : gate.made) leaked |= !weak.expired();
    expect(!leaked, "the results of decodes running at teardown are freed");

    Gate finished;
    {
        ImageDecodeQueue queue(finished.decoder());
        queue.submit(fake_png(4, 4), hash_of(1));
        expect(queue.wait_idle(5s), "a decode finishes");
    }
    leaked = false;
    for (const auto& weak : finished.made) leaked |= !weak.expired();
    expect(!leaked, "a finished result nobody took is freed at teardown");
}

void stress() {
    constexpr unsigned kImages = 400;
    Gate gate;
    ImageDecodeQueue queue(gate.decoder(), {.max_concurrent = 4, .max_inflight_bytes = 4096});
    std::vector<std::string> hashes;
    for (unsigned index = 0; index < kImages; ++index) hashes.push_back(hash_of(index));
    auto scene = scene_naming(hashes);

    std::atomic<bool> submitting{true};
    std::atomic<unsigned> taken{0};
    std::mutex taken_mutex;
    std::unordered_map<std::string, int> counts;
    const auto take = [&] {
        for (auto& decoded : queue.take_finished()) {
            expect(decoded.asset && decoded.asset->content_hash == decoded.content_hash,
                   "a taken asset carries its own hash");
            const std::lock_guard lock(taken_mutex);
            ++counts[decoded.content_hash];
            ++taken;
        }
    };
    std::thread submitter([&] {
        for (unsigned index = 0; index < kImages; ++index) {
            queue.submit(fake_png(16, 16), hashes[index]);
            if (index % 7 == 0) queue.cancel(hashes[index / 2]);
        }
        submitting = false;
    });
    std::thread taker([&] {
        while (submitting) {
            take();
            for (unsigned index = 0; index < 20; ++index) {
                (void)queue.is_pending(hashes[index]);
                (void)queue.stats();
            }
        }
    });
    submitter.join();
    taker.join();
    expect(queue.wait_idle(30s), "the stress run drains");
    take();
    bool once = true;
    for (const auto& entry : counts) once &= entry.second == 1;
    expect(once, "no result is handed out twice");
    const auto stats = queue.stats();
    expect(stats.peak_running <= 4U && stats.peak_inflight_bytes <= 4096U &&
               stats.queued == 0U && stats.running == 0U && stats.inflight_bytes == 0U,
           "the budgets held under load");

    // Without cancels every image arrives exactly once.
    Gate second;
    ImageDecodeQueue clean(second.decoder(), {.max_concurrent = 4});
    for (const auto& hash : hashes) clean.submit(fake_png(4, 4), hash);
    expect(clean.wait_idle(30s), "the clean run drains");
    Scene fresh = scene_naming(hashes);
    const auto published = publish_decoded_images(clean, fresh);
    expect(published.published == kImages && fresh.images.size() == kImages,
           "every image of a run without cancels is published once");
}

}  // namespace

int main() {
    refusals();
    decoding_does_not_block_the_caller();
    concurrency_budget();
    byte_budget();
    cancellation();
    publishing_rules();
    teardown_with_decodes_in_flight();
    stress();
    if (failures == 0) std::cout << "image decode queue tests passed\n";
    return failures == 0 ? 0 : 1;
}
