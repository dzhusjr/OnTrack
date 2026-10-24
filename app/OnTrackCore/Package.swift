// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "OnTrackCore",
    platforms: [.macOS(.v14)],
    products: [.library(name: "OnTrackCore", targets: ["OnTrackCore"])],
    targets: [
        .target(name: "OnTrackCore"),
        .testTarget(name: "OnTrackCoreTests", dependencies: ["OnTrackCore"]),
    ]
)
