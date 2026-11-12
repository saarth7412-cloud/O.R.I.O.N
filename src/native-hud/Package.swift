// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "OrionHUD",
    platforms: [
        .macOS(.v14)
    ],
    products: [
        .executable(name: "OrionHUD", targets: ["OrionHUD"])
    ],
    targets: [
        .executableTarget(
            name: "OrionHUD",
            path: "."
        )
    ]
)
