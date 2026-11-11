#pragma once
// A fixed set of worker threads owned by one CpuRenderInterface. Internal to
// the module.
//
// run() hands out bands [0, count) to the workers and to the calling thread,
// and returns once every band has run. Bands must touch disjoint memory; the
// caller decides what a band is (CpuRenderInterface uses horizontal strips of
// rows, which the rasteriser computes independently, so the output does not
// depend on which thread drew which band).

#include <condition_variable>
#include <cstddef>
#include <functional>
#include <mutex>
#include <thread>
#include <vector>

namespace vellum::rmlui::detail {

class Workers {
  public:
    // `threads` counts the calling thread: 1 starts no worker.
    explicit Workers(unsigned threads) {
        for (unsigned i = 1; i < threads; ++i)
            threads_.emplace_back([this] { work(); });
    }

    ~Workers() {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            stopping_ = true;
            ++generation_;
        }
        wake_.notify_all();
        for (auto& thread : threads_)
            thread.join();
    }

    Workers(const Workers&) = delete;
    Workers& operator=(const Workers&) = delete;

    [[nodiscard]] unsigned threads() const noexcept {
        return static_cast<unsigned>(threads_.size()) + 1u;
    }

    // Calls job(band) once for every band in [0, count), and returns when all
    // have returned.
    void run(unsigned count, const std::function<void(unsigned)>& job) {
        if (count == 0)
            return;
        if (threads_.empty() || count == 1) {
            for (unsigned band = 0; band < count; ++band)
                job(band);
            return;
        }
        std::size_t generation = 0;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            job_ = &job;
            count_ = count;
            next_ = 0;
            remaining_ = count;
            generation = ++generation_;
        }
        wake_.notify_all();
        take_bands(generation);
        std::unique_lock<std::mutex> lock(mutex_);
        done_.wait(lock, [this] { return remaining_ == 0; });
        job_ = nullptr;
    }

  private:
    void work() {
        std::size_t seen = 0;
        for (;;) {
            {
                std::unique_lock<std::mutex> lock(mutex_);
                wake_.wait(lock, [&] { return generation_ != seen; });
                seen = generation_;
                if (stopping_)
                    return;
            }
            take_bands(seen);
        }
    }

    // Takes bands of run `generation` until none is left. A band is claimed
    // under the lock together with a check of the generation, so a worker that
    // wakes late never runs a finished run's job.
    void take_bands(std::size_t generation) {
        for (;;) {
            const std::function<void(unsigned)>* job = nullptr;
            unsigned band = 0;
            {
                std::lock_guard<std::mutex> lock(mutex_);
                if (generation_ != generation || !job_ || next_ >= count_)
                    return;
                job = job_;
                band = next_++;
            }
            (*job)(band);
            std::lock_guard<std::mutex> lock(mutex_);
            if (--remaining_ == 0)
                done_.notify_all();
        }
    }

    std::vector<std::thread> threads_;
    std::mutex mutex_;
    std::condition_variable wake_;
    std::condition_variable done_;
    const std::function<void(unsigned)>* job_ = nullptr;
    unsigned count_ = 0;
    unsigned next_ = 0;
    unsigned remaining_ = 0;
    std::size_t generation_ = 0;
    bool stopping_ = false;
};

} // namespace vellum::rmlui::detail
