#include <pulp/events/event_loop.hpp>
#include <algorithm>
#include <utility>

namespace pulp::events {

struct EventLoop::State {
    struct TimedTask {
        TimePoint when;
        Task task;
    };

    std::mutex mutex;
    std::condition_variable cv;
    std::vector<Task> pending;
    std::vector<TimedTask> timed;
    std::atomic<bool> running{true};
    std::thread::id thread_id;
};

EventLoop::EventLoop()
    : state_(std::make_shared<State>()) {
    auto state = state_;
    thread_ = std::thread([state] { run(std::move(state)); });
    {
        std::lock_guard lock(state_->mutex);
        state_->thread_id = thread_.get_id();
    }
}

EventLoop::~EventLoop() {
    stop();
}

void EventLoop::dispatch(Task task) {
    auto state = state_;
    {
        std::lock_guard lock(state->mutex);
        if (!state->running.load(std::memory_order_acquire))
            return;
        state->pending.push_back(std::move(task));
    }
    state->cv.notify_one();
}

void EventLoop::dispatch_after(Duration delay, Task task) {
    auto when = Clock::now() + delay;
    auto state = state_;
    {
        std::lock_guard lock(state->mutex);
        if (!state->running.load(std::memory_order_acquire))
            return;
        state->timed.push_back({when, std::move(task)});
    }
    state->cv.notify_one();
}

bool EventLoop::is_current_thread() const {
    return std::this_thread::get_id() == thread_id();
}

std::thread::id EventLoop::thread_id() const {
    std::lock_guard lock(state_->mutex);
    return state_->thread_id;
}

bool EventLoop::running() const {
    return state_->running.load(std::memory_order_acquire);
}

void EventLoop::stop() {
    auto state = state_;
    auto should_notify = false;
    {
        std::lock_guard lock(state->mutex);
        should_notify = state->running.exchange(false, std::memory_order_acq_rel);
    }
    if (should_notify)
        state->cv.notify_one();

    if (!thread_.joinable())
        return;

    if (thread_.get_id() == std::this_thread::get_id()) {
        thread_.detach();
        return;
    }

    thread_.join();
}

void EventLoop::run(std::shared_ptr<State> state) {
    while (state->running.load(std::memory_order_acquire)) {
        std::vector<Task> tasks;
        {
            std::unique_lock lock(state->mutex);

            // Find the earliest timed task deadline
            auto deadline = TimePoint::max();
            for (const auto& t : state->timed) {
                if (t.when < deadline) deadline = t.when;
            }

            // Wait for work or deadline
            if (state->pending.empty() && (state->timed.empty() || Clock::now() < deadline)) {
                if (state->timed.empty()) {
                    state->cv.wait(lock, [&] {
                        return !state->pending.empty() || !state->timed.empty() ||
                               !state->running.load(std::memory_order_acquire);
                    });
                } else {
                    state->cv.wait_until(lock, deadline);
                }
            }

            if (!state->running.load(std::memory_order_acquire))
                break;

            // Collect ready immediate tasks
            tasks = std::move(state->pending);
            state->pending.clear();

            // Collect ready timed tasks
            auto now = Clock::now();
            auto it = std::partition(state->timed.begin(), state->timed.end(),
                [now](const State::TimedTask& t) { return t.when > now; });
            for (auto ready = it; ready != state->timed.end(); ++ready) {
                tasks.push_back(std::move(ready->task));
            }
            state->timed.erase(it, state->timed.end());
        }

        // Execute outside the lock
        for (auto& task : tasks) {
            if (state->running.load(std::memory_order_acquire) && task)
                task();
        }
    }
}

} // namespace pulp::events
