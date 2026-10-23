import Foundation

/// Golden files shared with the Python engine (single source of truth in engine/tests/fixtures/golden).
enum Golden {
    static var dir: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()  // OnTrackCoreTests
            .deletingLastPathComponent()  // Tests
            .deletingLastPathComponent()  // OnTrackCore
            .deletingLastPathComponent()  // app
            .deletingLastPathComponent()  // repo root
            .appendingPathComponent("engine/tests/fixtures/golden")
    }

    static func data(_ name: String) throws -> Data { try Data(contentsOf: dir.appendingPathComponent(name)) }
}
