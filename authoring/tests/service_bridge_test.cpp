#include <vellum/authoring/service_bridge.hpp>

#include <cstdlib>
#include <map>
#include <string>

namespace {

class FakeProvider final : public vellum::authoring::ServiceProvider {
public:
    vellum::authoring::ServiceResult request(
        const vellum::authoring::ServiceRequest& value) override {
        ++calls;
        return {
            .id = value.id,
            .ok = true,
            .value_json = R"({"text":"fixture"})",
        };
    }
    int calls = 0;
};

class MalformedProvider final : public vellum::authoring::ServiceProvider {
public:
    vellum::authoring::ServiceResult request(
        const vellum::authoring::ServiceRequest& value) override {
        return {
            .id = value.id,
            .ok = false,
            .error_code = "made-up",
        };
    }
};

void require(bool value) {
    if (!value) std::abort();
}

}  // namespace

int main() {
    FakeProvider provider;
    const std::map<std::string, std::string> enabled{
        {"files", "user-selected-text-v1"},
    };
    const vellum::authoring::ServiceRequest request{
        .id = "request-1",
        .service = "files",
        .operation = "selectText",
    };
    const auto success =
        vellum::authoring::dispatch_service(request, enabled, provider);
    require(success.ok);
    require(success.value_json == R"({"text":"fixture"})");
    require(provider.calls == 1);

    auto denied_capabilities = enabled;
    denied_capabilities["files"] = "denied";
    const auto denied = vellum::authoring::dispatch_service(
        request, denied_capabilities, provider);
    require(!denied.ok && denied.error_code == "capability-denied");
    require(provider.calls == 1);

    const auto unsupported = vellum::authoring::dispatch_service(
        request, {}, provider);
    require(!unsupported.ok && unsupported.error_code == "unsupported");
    require(provider.calls == 1);

    auto malformed = request;
    malformed.id = "bad";
    const auto invalid = vellum::authoring::dispatch_service(
        malformed, enabled, provider);
    require(!invalid.ok && invalid.error_code == "invalid-request");
    require(provider.calls == 1);

    const std::map<std::string, std::string> wrong_version{
        {"files", "text-v1"},
    };
    const auto version_error = vellum::authoring::dispatch_service(
        request, wrong_version, provider);
    require(!version_error.ok && version_error.error_code == "invalid-request");
    require(provider.calls == 1);

    MalformedProvider malformed_provider;
    const auto provider_error = vellum::authoring::dispatch_service(
        request, enabled, malformed_provider);
    require(!provider_error.ok && provider_error.error_code == "service-failed");
}
