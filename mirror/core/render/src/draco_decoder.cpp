#include <pulp/render/draco_decoder.hpp>

#ifdef PULP_HAS_DRACO

#include <draco/compression/decode.h>
#include <draco/mesh/mesh.h>
#include <draco/core/decoder_buffer.h>

namespace pulp::render {

namespace {

void copy_float_attribute(const draco::PointAttribute* attribute,
                          int component_count,
                          int vertex_count,
                          std::vector<float>& out) {
    if (attribute == nullptr || component_count <= 0) {
        return;
    }
    const int available_components = attribute->num_components();
    if (available_components <= 0) {
        return;
    }
    out.resize(static_cast<size_t>(vertex_count) *
               static_cast<size_t>(component_count));
    for (draco::PointIndex i(0); i < vertex_count; ++i) {
        float values[4] = {0.0f, 0.0f, 0.0f, 1.0f};
        attribute->GetMappedValue(i, values);
        const auto idx = static_cast<size_t>(i.value()) *
                         static_cast<size_t>(component_count);
        for (int component = 0; component < component_count; ++component) {
            if (component < available_components) {
                out[idx + static_cast<size_t>(component)] = values[component];
            } else if (component == 3) {
                out[idx + static_cast<size_t>(component)] = 1.0f;
            }
        }
    }
}

const draco::PointAttribute* attribute_by_unique_id(
    const draco::Mesh& mesh,
    int unique_id) {
    if (unique_id < 0) {
        return nullptr;
    }
    return mesh.GetAttributeByUniqueId(static_cast<uint32_t>(unique_id));
}

DecodedMesh decode_draco_impl(const uint8_t* data,
                              size_t size,
                              const DracoAttributeIds* attribute_ids) {
    DecodedMesh result;
    if (!data || size == 0) return result;

    draco::DecoderBuffer buffer;
    buffer.Init(reinterpret_cast<const char*>(data), size);

    auto type = draco::Decoder::GetEncodedGeometryType(&buffer);
    if (!type.ok() || type.value() != draco::TRIANGULAR_MESH) {
        return result;
    }

    draco::Decoder decoder;
    auto mesh_or = decoder.DecodeMeshFromBuffer(&buffer);
    if (!mesh_or.ok()) return result;

    auto& mesh = mesh_or.value();
    result.vertex_count = static_cast<int>(mesh->num_points());
    result.face_count = static_cast<int>(mesh->num_faces());

    if (attribute_ids != nullptr) {
        result.unique_id_attributes_applied = true;
        copy_float_attribute(attribute_by_unique_id(*mesh, attribute_ids->position),
                             3,
                             result.vertex_count,
                             result.positions);
        copy_float_attribute(attribute_by_unique_id(*mesh, attribute_ids->normal),
                             3,
                             result.vertex_count,
                             result.normals);
        copy_float_attribute(attribute_by_unique_id(*mesh, attribute_ids->texcoord0),
                             2,
                             result.vertex_count,
                             result.tex_coords);
        copy_float_attribute(attribute_by_unique_id(*mesh, attribute_ids->texcoord1),
                             2,
                             result.vertex_count,
                             result.tex_coords1);
        copy_float_attribute(attribute_by_unique_id(*mesh, attribute_ids->tangent),
                             4,
                             result.vertex_count,
                             result.tangents);
        copy_float_attribute(attribute_by_unique_id(*mesh, attribute_ids->color0),
                             4,
                             result.vertex_count,
                             result.colors);
    } else {
        copy_float_attribute(
            mesh->GetNamedAttribute(draco::GeometryAttribute::POSITION),
            3,
            result.vertex_count,
            result.positions);
        copy_float_attribute(
            mesh->GetNamedAttribute(draco::GeometryAttribute::NORMAL),
            3,
            result.vertex_count,
            result.normals);
        copy_float_attribute(
            mesh->GetNamedAttribute(draco::GeometryAttribute::TEX_COORD),
            2,
            result.vertex_count,
            result.tex_coords);
    }

    // Extract indices
    result.indices.resize(static_cast<size_t>(result.face_count) * 3);
    for (draco::FaceIndex i(0); i < mesh->num_faces(); ++i) {
        auto& face = mesh->face(i);
        auto idx = static_cast<size_t>(i.value()) * 3;
        result.indices[idx] = face[0].value();
        result.indices[idx + 1] = face[1].value();
        result.indices[idx + 2] = face[2].value();
    }

    result.success = true;
    return result;
}

} // namespace

DecodedMesh decode_draco(const uint8_t* data, size_t size) {
    return decode_draco_impl(data, size, nullptr);
}

DecodedMesh decode_draco(const uint8_t* data,
                         size_t size,
                         const DracoAttributeIds& attribute_ids) {
    return decode_draco_impl(data, size, &attribute_ids);
}

bool draco_decoder_available() {
    return true;
}

} // namespace pulp::render

#else // !PULP_HAS_DRACO

namespace pulp::render {

DecodedMesh decode_draco(const uint8_t*, size_t) {
    return {};  // DRACO not available
}

DecodedMesh decode_draco(const uint8_t*, size_t, const DracoAttributeIds&) {
    return {};  // DRACO not available
}

bool draco_decoder_available() {
    return false;
}

} // namespace pulp::render

#endif
