#pragma once

// SHA-256 (FIPS 180-4) over a byte span. The renderer keys its texture and
// image caches by content digest; this keeps the graphics package free of a
// platform crypto library or a framework dependency on every target.

#include <array>
#include <cstddef>
#include <cstdint>
#include <span>

namespace vellum::graphics::detail {

using Sha256Digest = std::array<std::uint8_t, 32>;

namespace sha256_impl {

inline constexpr std::array<std::uint32_t, 64> k{
    0x428a2f98U, 0x71374491U, 0xb5c0fbcfU, 0xe9b5dba5U, 0x3956c25bU, 0x59f111f1U,
    0x923f82a4U, 0xab1c5ed5U, 0xd807aa98U, 0x12835b01U, 0x243185beU, 0x550c7dc3U,
    0x72be5d74U, 0x80deb1feU, 0x9bdc06a7U, 0xc19bf174U, 0xe49b69c1U, 0xefbe4786U,
    0x0fc19dc6U, 0x240ca1ccU, 0x2de92c6fU, 0x4a7484aaU, 0x5cb0a9dcU, 0x76f988daU,
    0x983e5152U, 0xa831c66dU, 0xb00327c8U, 0xbf597fc7U, 0xc6e00bf3U, 0xd5a79147U,
    0x06ca6351U, 0x14292967U, 0x27b70a85U, 0x2e1b2138U, 0x4d2c6dfcU, 0x53380d13U,
    0x650a7354U, 0x766a0abbU, 0x81c2c92eU, 0x92722c85U, 0xa2bfe8a1U, 0xa81a664bU,
    0xc24b8b70U, 0xc76c51a3U, 0xd192e819U, 0xd6990624U, 0xf40e3585U, 0x106aa070U,
    0x19a4c116U, 0x1e376c08U, 0x2748774cU, 0x34b0bcb5U, 0x391c0cb3U, 0x4ed8aa4aU,
    0x5b9cca4fU, 0x682e6ff3U, 0x748f82eeU, 0x78a5636fU, 0x84c87814U, 0x8cc70208U,
    0x90befffaU, 0xa4506cebU, 0xbef9a3f7U, 0xc67178f2U};

[[nodiscard]] constexpr std::uint32_t rotr(std::uint32_t x, unsigned n) noexcept {
    return (x >> n) | (x << (32U - n));
}

inline void compress(std::array<std::uint32_t, 8>& h, const std::uint8_t* block) noexcept {
    std::array<std::uint32_t, 64> w{};
    for (std::size_t i = 0; i < 16; ++i) {
        w[i] = (std::uint32_t{block[i * 4]} << 24) | (std::uint32_t{block[i * 4 + 1]} << 16) |
               (std::uint32_t{block[i * 4 + 2]} << 8) | std::uint32_t{block[i * 4 + 3]};
    }
    for (std::size_t i = 16; i < 64; ++i) {
        const std::uint32_t s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >> 3);
        const std::uint32_t s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >> 10);
        w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    std::uint32_t a = h[0], b = h[1], c = h[2], d = h[3];
    std::uint32_t e = h[4], f = h[5], g = h[6], hh = h[7];
    for (std::size_t i = 0; i < 64; ++i) {
        const std::uint32_t s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
        const std::uint32_t ch = (e & f) ^ (~e & g);
        const std::uint32_t t1 = hh + s1 + ch + k[i] + w[i];
        const std::uint32_t s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
        const std::uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
        const std::uint32_t t2 = s0 + maj;
        hh = g;
        g = f;
        f = e;
        e = d + t1;
        d = c;
        c = b;
        b = a;
        a = t1 + t2;
    }
    h[0] += a; h[1] += b; h[2] += c; h[3] += d;
    h[4] += e; h[5] += f; h[6] += g; h[7] += hh;
}

}  // namespace sha256_impl

/// The SHA-256 of `bytes`.
[[nodiscard]] inline Sha256Digest sha256_digest(std::span<const std::uint8_t> bytes) noexcept {
    std::array<std::uint32_t, 8> h{0x6a09e667U, 0xbb67ae85U, 0x3c6ef372U, 0xa54ff53aU,
                                   0x510e527fU, 0x9b05688cU, 0x1f83d9abU, 0x5be0cd19U};
    const std::size_t full = bytes.size() / 64U;
    for (std::size_t i = 0; i < full; ++i) sha256_impl::compress(h, bytes.data() + i * 64U);

    // Tail: remaining bytes, the 0x80 marker, zero padding and the bit length.
    std::array<std::uint8_t, 128> tail{};
    const std::size_t rest = bytes.size() - full * 64U;
    for (std::size_t i = 0; i < rest; ++i) tail[i] = bytes[full * 64U + i];
    tail[rest] = 0x80U;
    const std::size_t tail_size = rest < 56U ? 64U : 128U;
    const std::uint64_t bits = static_cast<std::uint64_t>(bytes.size()) * 8U;
    for (std::size_t i = 0; i < 8; ++i) {
        tail[tail_size - 1U - i] = static_cast<std::uint8_t>(bits >> (8U * i));
    }
    sha256_impl::compress(h, tail.data());
    if (tail_size == 128U) sha256_impl::compress(h, tail.data() + 64);

    Sha256Digest digest{};
    for (std::size_t i = 0; i < 8; ++i) {
        digest[i * 4] = static_cast<std::uint8_t>(h[i] >> 24);
        digest[i * 4 + 1] = static_cast<std::uint8_t>(h[i] >> 16);
        digest[i * 4 + 2] = static_cast<std::uint8_t>(h[i] >> 8);
        digest[i * 4 + 3] = static_cast<std::uint8_t>(h[i]);
    }
    return digest;
}

}  // namespace vellum::graphics::detail
