#include <pulp/view/frame_clock.hpp>
#include <algorithm>

namespace pulp::view {

void FrameClock::tick(float dt) {
    if (dt < 0) dt = 0;
    dt_ = dt;
    time_ += dt;
    ++frame_;

    const auto count = subscribers_.size();

    for (std::size_t i = 0; i < count && i < subscribers_.size(); ++i) {
        if (!subscribers_[i].active) {
            continue;
        }

        const int id = subscribers_[i].id;
        auto callback = subscribers_[i].callback;
        const bool keep = callback(dt);

        auto it = std::find_if(subscribers_.begin(), subscribers_.end(),
            [id](const Subscriber& s) { return s.id == id; });
        if (it != subscribers_.end() && !keep) {
            it->active = false;
        }
    }

    // Compact dead subscribers
    subscribers_.erase(
        std::remove_if(subscribers_.begin(), subscribers_.end(),
            [](const Subscriber& s) { return !s.active; }),
        subscribers_.end());
}

int FrameClock::subscribe(std::function<bool(float dt)> callback) {
    int id = next_id_++;
    subscribers_.push_back({id, std::move(callback), true});
    return id;
}

void FrameClock::unsubscribe(int id) {
    for (auto& sub : subscribers_) {
        if (sub.id == id) {
            sub.active = false;
            break;
        }
    }
}

bool FrameClock::has_active_subscribers() const {
    for (auto& sub : subscribers_) {
        if (sub.active) return true;
    }
    return false;
}

void FrameClock::reset() {
    subscribers_.clear();
    time_ = 0;
    dt_ = 0;
    frame_ = 0;
    next_id_ = 1;
}

} // namespace pulp::view
