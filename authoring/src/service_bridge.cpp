#include <vellum/authoring/service_bridge.hpp>

#include <map>
#include <set>

namespace vellum::authoring {
namespace {

const std::map<std::string, std::string> kVersions{
    {"commands", "v1"},
    {"files", "user-selected-text-v1"},
    {"clipboard", "text-v1"},
    {"open_url", "external-v1"},
    {"persistence", "state-v1"},
};

const std::map<std::string, std::set<std::string>> kOperations{
    {"commands", {"execute"}},
    {"files", {"selectText"}},
    {"clipboard", {"readText", "writeText"}},
    {"open_url", {"openExternal"}},
    {"persistence", {"loadState", "saveState"}},
};

const std::set<std::string> kErrorCodes{
    "capability-denied",
    "cancelled",
    "invalid-request",
    "unsupported",
    "service-failed",
};

ServiceResult failure(
    const ServiceRequest& request,
    std::string code,
    std::string message) {
    return {
        .id = request.id,
        .ok = false,
        .error_code = std::move(code),
        .error_message = std::move(message),
    };
}

}  // namespace

ServiceResult dispatch_service(
    const ServiceRequest& request,
    const std::map<std::string, std::string>& capabilities,
    ServiceProvider& provider) {
    if (request.protocol != kServicesProtocol || request.kind != "request" ||
        request.id.rfind("request-", 0) != 0 || request.id.size() <= 8) {
        return failure(request, "invalid-request", "invalid service request envelope");
    }
    const auto version = kVersions.find(request.service);
    const auto operations = kOperations.find(request.service);
    if (version == kVersions.end() || operations == kOperations.end() ||
        !operations->second.contains(request.operation)) {
        return failure(request, "unsupported", "unsupported service or operation");
    }
    const auto declaration = capabilities.find(request.service);
    if (declaration == capabilities.end() || declaration->second == "unsupported") {
        return failure(request, "unsupported", "service capability is unsupported");
    }
    if (declaration->second == "denied") {
        return failure(request, "capability-denied", "service capability is denied");
    }
    if (declaration->second != version->second) {
        return failure(request, "invalid-request", "service capability version differs");
    }
    ServiceResult result = provider.request(request);
    if (result.protocol != kServicesProtocol || result.kind != "response" ||
        result.id != request.id) {
        return failure(request, "service-failed", "provider returned an invalid envelope");
    }
    if ((!result.ok &&
         (!kErrorCodes.contains(result.error_code) ||
          result.error_message.empty())) ||
        (result.ok &&
         (!result.error_code.empty() || !result.error_message.empty()))) {
        return failure(request, "service-failed", "provider returned an invalid result");
    }
    return result;
}

}  // namespace vellum::authoring
