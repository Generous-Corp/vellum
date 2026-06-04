#pragma once

#include <pulp/render/draco_decoder.hpp>
#include <pulp/scene/gltf_loader.hpp>

#include <utility>

namespace pulp::render {

inline scene::DracoDecodeCallback make_scene_draco_decode_callback() {
    return [](const uint8_t* data,
              size_t size,
              const scene::DracoAttributeIds& scene_ids) {
        DracoAttributeIds render_ids;
        render_ids.position = scene_ids.position;
        render_ids.normal = scene_ids.normal;
        render_ids.texcoord0 = scene_ids.texcoord0;
        render_ids.texcoord1 = scene_ids.texcoord1;
        render_ids.tangent = scene_ids.tangent;
        render_ids.color0 = scene_ids.color0;

        auto decoded = decode_draco(data, size, render_ids);

        scene::DracoDecodedMesh out;
        out.positions = std::move(decoded.positions);
        out.normals = std::move(decoded.normals);
        out.texcoord0 = std::move(decoded.tex_coords);
        out.texcoord1 = std::move(decoded.tex_coords1);
        out.tangents = std::move(decoded.tangents);
        out.color0 = std::move(decoded.colors);
        out.indices = std::move(decoded.indices);
        out.decoder_available = draco_decoder_available();
        out.success = decoded.success && decoded.unique_id_attributes_applied;
        return out;
    };
}

} // namespace pulp::render
