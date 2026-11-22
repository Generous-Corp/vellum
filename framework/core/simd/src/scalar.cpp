// vellum::simd kernels, scalar reference backend. The loops live in
// <vellum/simd/scalar_kernels.hpp> so the header fallback shares them.

#include <vellum/simd/scalar_kernels.hpp>
#include <vellum/simd/simd.hpp>

namespace vellum::simd::backend::scalar {

PULP_SIMD_SCALAR_DEFINE(, float)
PULP_SIMD_SCALAR_DEFINE(, double)
PULP_SIMD_SCALAR_DEFINE_COMPLEX(, float)
PULP_SIMD_SCALAR_DEFINE_COMPLEX(, double)
PULP_SIMD_SCALAR_DEFINE_MATH(, float, )
PULP_SIMD_SCALAR_DEFINE_MATH(, double, )

} // namespace vellum::simd::backend::scalar
