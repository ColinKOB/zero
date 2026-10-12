// swift-tools-version: 6.0
// The Mac bench and tests for Zero's On-Device Voice. It compiles the app's own files (symlinks into
// ../Taffy/Voice/OnDevice), so what it measures is what the app runs.
//
//   swift test                                   the text rules (sentences, questions, interruptions, turn ends)
//   swift run -c release OnDeviceBench voice     load the voice, greeting takes, replies through the app's speaker
//
// FLUIDAUDIO_PATH=/path/to/checkout uses a local copy of FluidAudio instead of cloning the pinned commit.
import Foundation
import PackageDescription

let localFluid = ProcessInfo.processInfo.environment["FLUIDAUDIO_PATH"]
let fluid: Package.Dependency = localFluid.map { .package(path: $0) }
    ?? .package(url: "https://github.com/FluidInference/FluidAudio", revision: "0c0f113e8db4b862b19a99ce9fd7c9da73324f5f")
let fluidName = localFluid.map { URL(fileURLWithPath: $0).lastPathComponent } ?? "FluidAudio"

let package = Package(
    name: "OnDeviceBench",
    platforms: [.macOS(.v15)],
    dependencies: [fluid],
    targets: [
        .executableTarget(
            name: "OnDeviceBench",
            dependencies: [.product(name: "FluidAudio", package: fluidName)],
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
        .testTarget(
            name: "OnDeviceTextTests",
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
    ]
)
