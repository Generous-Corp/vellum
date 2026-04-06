#pragma once

#include <pulp/runtime/spsc_queue.hpp>
#include <functional>
#include <chrono>
#include <vector>
#include <mutex>
#include <atomic>
#include <thread>
#include <condition_variable>

namespace pulp::events {

using Clock = std::chrono::steady_clock;
using TimePoint = Clock::time_point;
using Duration = Clock::duration;
using Task = std::function<void()>;

// Non-singleton event loop. Create as many as you need.
// Each EventLoop owns a thread for dispatching work.
class EventLoop {
public:
    EventLoop();
    ~EventLoop();

    EventLoop(const EventLoop&) = delete;
    EventLoop& operator=(const EventLoop&) = delete;

    // Dispatch a task to be executed on this event loop's thread
    void dispatch(Task task);

    // Schedule a one-shot task after a delay
    void dispatch_after(Duration delay, Task task);

    // Check if the calling thread is this event loop's thread
    bool is_current_thread() const;

    // Stop the event loop (called automatically by destructor)
    void stop();

    // Is the loop running?
    bool running() const { return running_.load(std::memory_order_acquire); }

private:
    struct TimedTask {
        TimePoint when;
        Task task;
        bool operator>(const TimedTask& other) const { return when > other.when; }
    };

    void run();

    std::thread thread_;
    std::mutex mutex_;
    std::condition_variable cv_;
    std::vector<Task> pending_;
    std::vector<TimedTask> timed_;
    std::atomic<bool> running_{true};
    std::thread::id thread_id_;
};

} // namespace pulp::events
