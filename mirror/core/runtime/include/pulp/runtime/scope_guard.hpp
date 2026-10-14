#pragma once

#include <utility>

namespace pulp::runtime {

// RAII scope guard — executes a callable on destruction
template<typename F>
class ScopeGuard {
public:
    explicit ScopeGuard(F&& fn) : fn_(std::move(fn)), active_(true) {}
    ~ScopeGuard() { if (active_) fn_(); }

    ScopeGuard(ScopeGuard&& other) noexcept
        : fn_(std::move(other.fn_)), active_(other.active_) {
        other.dismiss();
    }

    ScopeGuard(const ScopeGuard&) = delete;
    ScopeGuard& operator=(const ScopeGuard&) = delete;
    ScopeGuard& operator=(ScopeGuard&&) = delete;

    void dismiss() { active_ = false; }

private:
    F fn_;
    bool active_;
};

template<typename F>
ScopeGuard<F> make_scope_guard(F&& fn) {
    return ScopeGuard<F>(std::forward<F>(fn));
}

} // namespace pulp::runtime

// Macro for anonymous scope guards
#define PULP_CONCAT_IMPL(a, b) a##b
#define PULP_CONCAT(a, b) PULP_CONCAT_IMPL(a, b)
#define PULP_ON_SCOPE_EXIT(code) \
    auto PULP_CONCAT(_scope_guard_, __LINE__) = \
        ::pulp::runtime::make_scope_guard([&]() { code; })
