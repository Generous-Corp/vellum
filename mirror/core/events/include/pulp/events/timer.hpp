#pragma once

#include <pulp/events/event_loop.hpp>
#include <functional>
#include <atomic>
#include <memory>

namespace pulp::events {

// Timer bound to an EventLoop. Supports one-shot and repeating.
class Timer {
public:
    using Callback = std::function<void()>;

    Timer(EventLoop& loop, Duration interval, Callback callback, bool repeating = true);
    ~Timer();

    Timer(const Timer&) = delete;
    Timer& operator=(const Timer&) = delete;

    void start();
    void stop();
    bool is_active() const { return active_.load(std::memory_order_acquire); }

    void set_interval(Duration interval);
    Duration interval() const { return interval_; }

private:
    void schedule_next();

    EventLoop& loop_;
    Duration interval_;
    Callback callback_;
    bool repeating_;
    std::atomic<bool> active_{false};
    std::shared_ptr<std::atomic<bool>> alive_; // prevent use-after-free on stop
};

} // namespace pulp::events
