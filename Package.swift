// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "MurmurMark",
    platforms: [
        .macOS(.v15),
    ],
    products: [
        .executable(name: "murmurmark", targets: ["MurmurMarkCLI"]),
    ],
    targets: [
        .executableTarget(
            name: "MurmurMarkCLI",
            path: "Sources/MurmurMarkCLI"
        ),
    ]
)
