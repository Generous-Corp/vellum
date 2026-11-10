#include <vellum/dataplane/host_surfaces.hpp>

#include <cassert>
#include <cstdio>

#include <vellum/runtime/scoped_no_alloc.hpp>

namespace vellum::dataplane {

void HostParamSurface::assert_call_context(const char* op) {
#ifndef NDEBUG
    // paint_all runs inside a ScopedNoAlloc; a host-surface call from paint
    // would re-enter arbitrary host formatter/getter code mid-render-traversal.
    // See the class-level threading contract.
    if (vellum::runtime::is_in_no_alloc_scope()) {
        std::fprintf(stderr,
                     "[vellum::dataplane] HostParamSurface::%s called from paint()/no-alloc scope — "
                     "host-param access is legal only from tick/update. Snapshot values at "
                     "tick and paint from the snapshot.\n",
                     op);
        assert(false && "HostParamSurface call from paint()/no-alloc scope");
    }
#else
    (void)op;
#endif
}

void HostActionSurface::assert_call_context(const char* op) {
#ifndef NDEBUG
    if (vellum::runtime::is_in_no_alloc_scope()) {
        std::fprintf(stderr,
                     "[vellum::dataplane] HostActionSurface::%s called from paint()/no-alloc scope — "
                     "host actions are legal only from tick/update.\n",
                     op);
        assert(false && "HostActionSurface call from paint()/no-alloc scope");
    }
#else
    (void)op;
#endif
}

} // namespace vellum::dataplane
