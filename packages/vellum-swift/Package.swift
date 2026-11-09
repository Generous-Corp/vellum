// swift-tools-version:6.2
import PackageDescription

let package = Package(
    name: "VellumSwift",
    platforms: [.macOS("13.3")],
    products: [
        .library(name: "VellumHost", targets: ["VellumHost"]),
        .executable(name: "vellum-swift-proof", targets: ["VellumSwiftProof"]),
    ],
    targets: [
        .systemLibrary(
            name: "CVellumHostC",
            path: "Sources/CVellumHostC",
            pkgConfig: "vellum-host-c"),
        .target(name: "VellumHost", dependencies: ["CVellumHostC"]),
        .executableTarget(name: "VellumSwiftProof", dependencies: ["VellumHost"]),
    ]
)
