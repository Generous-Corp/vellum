# Canvas raster parity

`pulp-raster-parity.pam` is a 96 × 52 pixel CPU raster from Pulp
`bac5a9fa7d00b8af2fb6dd6e1174927e34307a09`. The Pulp canvas tree at that
revision matches the canvas tree at `0d6070c1d00999ae12abdaff549b50b6c89c4502`.
The SDK uses Skia `be7d7c3e996e567eda7dc9774274f7a0103cdb8c`, which is also
the Vellum renderer lock. The scene calls live in
[canvas_raster_scene.hpp](../../tools/framework-tests/canvas_raster_scene.hpp).
The fixture covers a fractional round-capped arc, an even-odd SVG icon and a
translucent gradient stroke blended over an opaque background. The test
compares every RGBA channel at zero tolerance. It also checks the internal
sample count passed to the editor's Graphite context.

Run `ctest --test-dir build-parity -R '^vellum[.]framework[.]canvas-raster-parity$'
--output-on-failure` after building the framework test target.

## Generate the reference

From Vellum's root, use an installed Pulp SDK at the revision above.
Set `PULP_SDK` to that prefix. If the SDK exports its Vellum integration,
also set `VELLUM_SDK` to the Vellum prefix used to build it.

```sh
vellum_source="$PWD"
reference_dir="$PWD/build-pulp-raster-reference"
mkdir -p "$reference_dir"
cat > "$reference_dir/CMakeLists.txt" <<'CMAKE'
cmake_minimum_required(VERSION 3.24)
project(PulpRasterReference LANGUAGES CXX)
find_package(Pulp CONFIG REQUIRED)
add_executable(pulp-raster-reference main.cpp)
target_include_directories(pulp-raster-reference PRIVATE
    "${VELLUM_SOURCE}/tools/framework-tests")
target_compile_features(pulp-raster-reference PRIVATE cxx_std_20)
target_link_libraries(pulp-raster-reference PRIVATE Pulp::canvas)
CMAKE
cat > "$reference_dir/main.cpp" <<'CPP'
#include "canvas_raster_scene.hpp"
#include <pulp/canvas/skia_canvas.hpp>
#include <pulp/canvas/skia_text_raster_policy.hpp>
#include "include/core/SkColorSpace.h"
#include "include/core/SkPixmap.h"
#include "include/core/SkSurface.h"
#include <cstdint>
#include <fstream>
#include <vector>

int main(int argc, char** argv) {
    if (argc != 2) return 1;
    namespace canvas = pulp::canvas;
    using namespace canvas_raster_scene;
    const auto info = SkImageInfo::Make(width, height, kRGBA_8888_SkColorType,
                                        kPremul_SkAlphaType, SkColorSpace::MakeSRGB());
    const auto props = canvas::surface_props_for(canvas::TextRasterPolicy::legacy);
    const auto surface = SkSurfaces::Raster(info, &props);
    if (!surface) return 1;
    canvas::SkiaCanvas target(surface->getCanvas());
    if (!paint<canvas::Color, canvas::LineCap>(target)) return 1;
    std::vector<std::uint8_t> rgba(height * row_bytes);
    const SkPixmap pixels(info, rgba.data(), row_bytes);
    if (!surface->readPixels(pixels, 0, 0)) return 1;
    std::ofstream output(argv[1], std::ios::binary);
    output << pam_header;
    output.write(reinterpret_cast<const char*>(rgba.data()), rgba.size());
    output.close();
    return !output;
}
CPP
~/bin/leased-build -- cmake -S "$reference_dir" -B "$reference_dir/build" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_OSX_DEPLOYMENT_TARGET=13.4 \
  -DVELLUM_SOURCE="$vellum_source" "-DCMAKE_PREFIX_PATH=$PULP_SDK;${VELLUM_SDK:-}"
~/bin/leased-build -- cmake --build "$reference_dir/build"
"$reference_dir/build/pulp-raster-reference" "$reference_dir/pulp-raster-parity.pam"
cmp "$reference_dir/pulp-raster-parity.pam" fixtures/canvas/pulp-raster-parity.pam
```

## Check Spectr on Metal

Use a Spectr standalone relinked against the Vellum SDK under test. Set
`SPECTR_VELLUM_BIN` to its executable and `PULP_ORACLE` to the
990 × 645 legacy Metal PNG. Run on the same macOS Metal host with the
legacy capture's assets, backing scale and startup state.

```sh
capture_dir="$PWD/build-spectr-parity"
mkdir -p "$capture_dir"
for run in 1 2; do
    PULP_HEADLESS=1 PULP_FRAMES=120 \
        PULP_SCREENSHOT="$capture_dir/spectr-$run.png" "$SPECTR_VELLUM_BIN" || exit
done
python3 - "$PULP_ORACLE" "$capture_dir/spectr-1.png" "$capture_dir/spectr-2.png" <<'PY'
import sys
from PIL import Image, ImageChops

oracle, first, second = [Image.open(path).convert("RGBA") for path in sys.argv[1:]]
if any(image.size != (990, 645) for image in (oracle, first, second)):
    sys.exit("expected three 990x645 captures")
counts = []
for name, a, b in (("rerun", first, second), ("legacy", oracle, first)):
    delta = ImageChops.difference(a, b).tobytes()
    count = sum(any(delta[i:i+4]) for i in range(0, len(delta), 4))
    above_16 = sum(max(delta[i:i+4]) > 16 for i in range(0, len(delta), 4))
    print(f"{name}: {count} differing pixels, {above_16} above 16/255")
    counts.append(count)
sys.exit(any(counts))
PY
```
