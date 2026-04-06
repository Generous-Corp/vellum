#include <pulp/events/timer.hpp>

namespace pulp::events {

Timer::Timer(EventLoop& loop, Duration interval, Callback callback, bool repeating)
    : loop_(loop)
    , interval_(interval)
    , callback_(std::move(callback))
    , repeating_(repeating)
    , alive_(std::make_shared<std::atomic<bool>>(true))
{
}

Timer::~Timer() {
    stop();
}

void Timer::start() {
    active_.store(true, std::memory_order_release);
    schedule_next();
}

void Timer::stop() {
    active_.store(false, std::memory_order_release);
    alive_->store(false, std::memory_order_release);
    // Create a new sentinel for potential reuse
    alive_ = std::make_shared<std::atomic<bool>>(true);
}

void Timer::set_interval(Duration interval) {
    interval_ = interval;
}

void Timer::schedule_next() {
    auto alive = alive_;
    auto* self = this;
    loop_.dispatch_after(interval_, [self, alive]() {
        if (!alive->load(std::memory_order_acquire))
            return;
        if (!self->active_.load(std::memory_order_acquire))
            return;
        self->callback_();
        if (self->repeating_ && self->active_.load(std::memory_order_acquire)) {
            self->schedule_next();
        }
    });
}

} // namespace pulp::events
