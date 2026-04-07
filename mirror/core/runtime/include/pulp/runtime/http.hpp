#pragma once

// HTTP client — wraps cpp-httplib for GET/POST with timeout and TLS.

#include <string>
#include <string_view>
#include <vector>
#include <map>
#include <optional>
#include <cstdint>

namespace pulp::runtime {

struct HttpResponse {
    int status_code = 0;
    std::string body;
    std::map<std::string, std::string> headers;
    std::string error;

    bool ok() const { return status_code >= 200 && status_code < 300; }
};

/// Perform an HTTP GET request.
HttpResponse http_get(std::string_view url,
                      int timeout_seconds = 30);

/// Perform an HTTP POST request with a body.
HttpResponse http_post(std::string_view url,
                       std::string_view body,
                       std::string_view content_type = "application/json",
                       int timeout_seconds = 30);

/// Download a file from a URL to a local path. Returns true on success.
bool http_download(std::string_view url, std::string_view output_path,
                   int timeout_seconds = 60);

}  // namespace pulp::runtime
