#pragma once

#include <pulp/runtime/spsc_queue.hpp>
#include <functional>
#include <chrono>
#include <vector>
#include <mutex>
#include <atomic>
#include <thread>
#include <condition_variable>
#include <memory>

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

    // Dispatch a task to be executed on this event loop's thread.
    // Tasks submitted after stop() are ignored.
    void dispatch(Task task);

    // Schedule a one-shot task after a delay. Tasks submitted after stop()
    // are ignored.
    void dispatch_after(Duration delay, Task task);

    // Check if the calling thread is this event loop's thread
    bool is_current_thread() const;
    std::thread::id thread_id() const;

    // Stop the event loop (called automatically by destructor)
    void stop();

    // Is the loop running?
    bool running() const;

private:
    struct State;

    static void run(std::shared_ptr<State> state);

    std::shared_ptr<State> state_;
    std::thread thread_;
};

} // namespace pulp::events
