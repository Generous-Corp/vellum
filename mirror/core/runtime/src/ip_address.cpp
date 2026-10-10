#include <pulp/runtime/ip_address.hpp>
#include <cstring>

#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#include <iphlpapi.h>
#pragma comment(lib, "ws2_32.lib")
#pragma comment(lib, "iphlpapi.lib")
#else
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <ifaddrs.h>
#include <unistd.h>
#include <netdb.h>
#endif

namespace pulp::runtime {

bool is_valid_ipv4(std::string_view address) {
    struct in_addr addr;
    std::string addr_str(address);
    return inet_pton(AF_INET, addr_str.c_str(), &addr) == 1;
}

std::vector<std::string> local_ipv4_addresses() {
    std::vector<std::string> addresses;

#ifdef _WIN32
    char hostname_buf[256];
    if (gethostname(hostname_buf, sizeof(hostname_buf)) != 0) return addresses;

    struct addrinfo hints{}, *result;
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_STREAM;

    if (getaddrinfo(hostname_buf, nullptr, &hints, &result) != 0) return addresses;

    for (auto* rp = result; rp; rp = rp->ai_next) {
        char ip[INET_ADDRSTRLEN];
        auto* sin = reinterpret_cast<struct sockaddr_in*>(rp->ai_addr);
        inet_ntop(AF_INET, &sin->sin_addr, ip, sizeof(ip));
        if (std::string_view(ip) != "127.0.0.1")
            addresses.push_back(ip);
    }
    freeaddrinfo(result);
#else
    struct ifaddrs* ifaddr;
    if (getifaddrs(&ifaddr) != 0) return addresses;

    for (auto* ifa = ifaddr; ifa; ifa = ifa->ifa_next) {
        if (!ifa->ifa_addr || ifa->ifa_addr->sa_family != AF_INET) continue;

        char ip[INET_ADDRSTRLEN];
        auto* sin = reinterpret_cast<struct sockaddr_in*>(ifa->ifa_addr);
        inet_ntop(AF_INET, &sin->sin_addr, ip, sizeof(ip));

        if (std::string_view(ip) != "127.0.0.1")
            addresses.push_back(ip);
    }
    freeifaddrs(ifaddr);
#endif

    return addresses;
}

std::string local_ipv4_address() {
    auto addrs = local_ipv4_addresses();
    return addrs.empty() ? "127.0.0.1" : addrs[0];
}

std::string hostname() {
    char buf[256];
    if (gethostname(buf, sizeof(buf)) == 0)
        return buf;
    return "localhost";
}

}  // namespace pulp::runtime
