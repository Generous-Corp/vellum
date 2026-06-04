#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

namespace pulp::render {

/// Decoded mesh geometry from DRACO-compressed data.
struct DecodedMesh {
    std::vector<float> positions;      ///< Flat xyz positions
    std::vector<float> normals;        ///< Flat xyz normals (may be empty)
    std::vector<float> tex_coords;     ///< Flat uv coordinates (may be empty)
    std::vector<float> tex_coords1;    ///< Flat secondary uv coordinates (may be empty)
    std::vector<float> tangents;       ///< Flat xyzw tangents (may be empty)
    std::vector<float> colors;         ///< Flat rgba vertex colors (may be empty)
    std::vector<uint32_t> indices;     ///< Triangle indices
    int vertex_count = 0;
    int face_count = 0;
    bool success = false;
    bool unique_id_attributes_applied = false;
};

/// Draco attribute unique IDs from a glTF KHR_draco_mesh_compression
/// attributes map. Negative values mean the semantic is not requested.
struct DracoAttributeIds {
    int position = -1;
    int normal = -1;
    int texcoord0 = -1;
    int texcoord1 = -1;
    int tangent = -1;
    int color0 = -1;
};

/// Decode a DRACO-compressed mesh buffer.
/// Returns decoded geometry with positions, normals, UVs, and indices.
DecodedMesh decode_draco(const uint8_t* data, size_t size);

/// Decode a DRACO-compressed mesh buffer using glTF semantic -> Draco unique-id
/// mappings. This is the path required by KHR_draco_mesh_compression.
DecodedMesh decode_draco(const uint8_t* data,
                         size_t size,
                         const DracoAttributeIds& attribute_ids);

/// True when this build was compiled with the native DRACO decoder enabled.
bool draco_decoder_available();

} // namespace pulp::render
