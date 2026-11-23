#include <vellum/authoring/design_pipeline.hpp>
#include <vellum/view/design_ir_io.hpp>

#include <iostream>
#include <iterator>
#include <string_view>

int main(int argc, char** argv) {
    using namespace vellum::authoring;
    if (argc != 2) return 2;

    ImportInput input;
    const std::string_view source = argv[1];
    if (source == "pencil") input.source = DesignSource::pencil_json;
    else if (source == "designmd") input.source = DesignSource::designmd;
    else if (source == "v0") input.source = DesignSource::v0_tsx;
    else return 2;

    input.payload.assign(std::istreambuf_iterator<char>(std::cin), {});
    const auto result = import_design(input);
    std::cout << '[' << import_result_to_json(result) << ',';
    if (result.ir) std::cout << vellum::view::serialize_design_ir(*result.ir);
    else std::cout << "null";
    std::cout << "]\n";
    return std::cout ? 0 : 1;
}
