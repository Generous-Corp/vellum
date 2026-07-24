#pragma once

#include <map>
#include <string>
#include <string_view>

namespace vellum::authoring {

inline constexpr std::string_view kServicesProtocol = "vellum.services.v1";

struct ServiceRequest final {
    std::string protocol = std::string(kServicesProtocol);
    std::string kind = "request";
    std::string id;
    std::string service;
    std::string operation;
    std::string arguments_json = "{}";
};

struct ServiceResult final {
    std::string protocol = std::string(kServicesProtocol);
    std::string kind = "response";
    std::string id;
    bool ok = false;
    std::string value_json;
    std::string error_code;
    std::string error_message;
};

class ServiceProvider {
public:
    virtual ~ServiceProvider() = default;
    [[nodiscard]] virtual ServiceResult request(const ServiceRequest& value) = 0;
};

/// Engine-neutral capability gate. JavaScriptCore, Wasm, or another engine can
/// serialize these same envelopes without embedding provider behavior.
[[nodiscard]] ServiceResult dispatch_service(
    const ServiceRequest& request,
    const std::map<std::string, std::string>& capabilities,
    ServiceProvider& provider);

}  // namespace vellum::authoring
