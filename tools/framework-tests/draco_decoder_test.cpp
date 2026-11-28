// vellum::render::decode_draco against a real Draco stream: one triangle with
// POSITION, NORMAL and TEXCOORD_0 under glTF unique ids 7, 8 and 9. The bytes
// are the KHR_draco_mesh_compression buffer view of Pulp's DracoTriangle.glb
// test asset.
//
// The decoder is built in only with VELLUM_ENABLE_DRACO. Without it the same
// test is the control: it expects the stub (unavailable, an empty mesh) and
// fails if a decode succeeds, so a build can neither claim Draco it does not
// link nor lose Draco it was asked to link without this test failing.

#include "framework_test.hpp"

#include <vellum/render/draco_decoder.hpp>

#include <cstdint>
#include <vector>

#ifndef VELLUM_TEST_EXPECT_DRACO
#error "VELLUM_TEST_EXPECT_DRACO must be 0 or 1"
#endif

namespace {

constexpr std::uint8_t kTriangle[] = {
    0x44, 0x52, 0x41, 0x43, 0x4f, 0x02, 0x02, 0x01, 0x00, 0x00, 0x00, 0x01,
    0x03, 0x01, 0x00, 0x01, 0x02, 0x01, 0x03, 0x00, 0x09, 0x03, 0x00, 0x07,
    0x01, 0x09, 0x03, 0x00, 0x08, 0x03, 0x09, 0x02, 0x00, 0x09, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x80, 0xbf, 0x00, 0x00, 0x80, 0xbf, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x80, 0x3f, 0x00, 0x00, 0x80, 0xbf, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x80, 0x3f, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x80,
    0x3f, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x80,
    0x3f, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x80,
    0x3f, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x80,
    0x3f, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x3f, 0x00, 0x00, 0x80,
    0x3f
};

// One triangle whose attribute 0 is a 6-component float32 (the decoder reads 3)
// and whose attribute 1 is a 2-component uint16 (10..60). Encoded with Draco
// 1.5.9 sequential encoding, so the values are stored unquantized. A decoder that
// copies the stored bytes instead of converting to float, or copies more than
// the components it was asked for, overruns its buffer or returns garbage here.
constexpr std::uint8_t kWideAndInteger[] = {
    0x44, 0x52, 0x41, 0x43, 0x4f, 0x02, 0x02, 0x01, 0x00, 0x00, 0x00, 0x01,
    0x03, 0x01, 0x00, 0x01, 0x02, 0x01, 0x02, 0x00, 0x09, 0x06, 0x00, 0x00,
    0x03, 0x04, 0x02, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00, 0x80, 0x3f, 0x00,
    0x00, 0x00, 0x40, 0x00, 0x00, 0x40, 0x40, 0x00, 0x00, 0x80, 0x40, 0x00,
    0x00, 0xa0, 0x40, 0x00, 0x00, 0xc0, 0x40, 0x00, 0x00, 0xe0, 0x40, 0x00,
    0x00, 0x00, 0x41, 0x00, 0x00, 0x10, 0x41, 0x00, 0x00, 0x20, 0x41, 0x00,
    0x00, 0x30, 0x41, 0x00, 0x00, 0x40, 0x41, 0x00, 0x00, 0x50, 0x41, 0x00,
    0x00, 0x60, 0x41, 0x00, 0x00, 0x70, 0x41, 0x00, 0x00, 0x80, 0x41, 0x00,
    0x00, 0x88, 0x41, 0x00, 0x00, 0x90, 0x41, 0x00, 0x01, 0x01, 0x01, 0x01,
    0x29, 0xad, 0x0a, 0x4b, 0xad, 0x0a, 0x4b, 0xa9, 0x2a, 0x03, 0x99, 0xb1,
    0xb1, 0x0a, 0x00, 0x00, 0x00, 0x3c, 0x00, 0x00, 0x00
};

vellum::render::DracoAttributeIds triangle_ids() {
    vellum::render::DracoAttributeIds ids;
    ids.position = 7;
    ids.normal = 8;
    ids.texcoord0 = 9;
    return ids;
}

} // namespace

FRAMEWORK_TEST(draco_availability_matches_the_build) {
    CHECK(vellum::render::draco_decoder_available() == (VELLUM_TEST_EXPECT_DRACO == 1));
}

FRAMEWORK_TEST(draco_decodes_a_triangle_with_its_gltf_ids_or_is_the_stub) {
    const auto mesh = vellum::render::decode_draco(kTriangle, sizeof(kTriangle), triangle_ids());
    if (VELLUM_TEST_EXPECT_DRACO == 1) {
        CHECK(mesh.success);
        CHECK(mesh.unique_id_attributes_applied);
        CHECK(mesh.vertex_count == 3);
        CHECK(mesh.face_count == 1);
        CHECK((mesh.positions == std::vector<float>{-1, -1, 0, 1, -1, 0, 0, 1, 0}));
        CHECK((mesh.normals == std::vector<float>{0, 0, 1, 0, 0, 1, 0, 0, 1}));
        CHECK((mesh.tex_coords == std::vector<float>{0, 0, 1, 0, 0.5f, 1}));
        CHECK((mesh.indices == std::vector<std::uint32_t>{0, 1, 2}));
    } else {
        CHECK(!mesh.success);
        CHECK(mesh.positions.empty());
        CHECK(mesh.indices.empty());
    }
}

FRAMEWORK_TEST(draco_refuses_bytes_that_are_not_a_draco_mesh) {
    const std::uint8_t garbage[] = {0, 1, 2, 3};
    CHECK(!vellum::render::decode_draco(garbage, sizeof(garbage), triangle_ids()).success);
    CHECK(!vellum::render::decode_draco(nullptr, 0, triangle_ids()).success);
    // The stream with its magic overwritten is not a mesh either.
    std::vector<std::uint8_t> broken(std::begin(kTriangle), std::end(kTriangle));
    for (int i = 0; i < 5; ++i)
        broken[static_cast<std::size_t>(i)] = 0;
    CHECK(!vellum::render::decode_draco(broken.data(), broken.size(), triangle_ids()).success);
}

FRAMEWORK_TEST(draco_converts_wide_and_integer_attributes_without_overrunning) {
    vellum::render::DracoAttributeIds ids;
    ids.position = 0;
    ids.texcoord0 = 1;
    const auto mesh = vellum::render::decode_draco(kWideAndInteger, sizeof(kWideAndInteger), ids);
    if (VELLUM_TEST_EXPECT_DRACO == 1) {
        CHECK(mesh.success);
        CHECK(mesh.vertex_count == 3);
        // Only the first three of the six stored components are read.
        CHECK((mesh.positions == std::vector<float>{1, 2, 3, 7, 8, 9, 13, 14, 15}));
        // Integer values are converted to float, not reinterpreted. They are not
        // normalized: a normalized integer attribute keeps its raw integer range.
        CHECK((mesh.tex_coords == std::vector<float>{10, 20, 30, 40, 50, 60}));
    } else {
        CHECK(!mesh.success);
    }
}

int main() { return framework_test::run_all(); }
