// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "Radar",
    platforms: [.macOS("26.0")],
    targets: [
        .executableTarget(
            name: "Radar",
            path: "Sources/Radar",
            swiftSettings: [.swiftLanguageMode(.v5)]
        )
    ]
)
