import Foundation

/// The heavy post-session work (speaker separation, large-v3 re-transcription, summary) can wait for mains power.
public enum PowerPolicy {
    public static func deferFinalize(_ action: FinalizeAction, onBattery: Bool, waitForPower: Bool) -> Bool {
        action != .discard && onBattery && waitForPower
    }
}

/// Sessions saved on battery, waiting to be processed once the Mac is plugged in. Persisted as JSON.
public struct FinalizeQueue: Equatable {
    public struct Item: Equatable, Codable {
        public let sessionId: String
        public let action: String

        public init(sessionId: String, action: FinalizeAction) {
            self.sessionId = sessionId
            self.action = action.rawValue
        }

        public var finalizeAction: FinalizeAction { FinalizeAction(rawValue: action) ?? .saveAll }
    }

    public private(set) var items: [Item] = []

    public init() {}

    public init(json: Data) throws {
        items = try JSONDecoder().decode([Item].self, from: json)
    }

    public init(jsonOrEmpty: Data?) {
        items = jsonOrEmpty.flatMap { try? JSONDecoder().decode([Item].self, from: $0) } ?? []
    }

    public func json() -> Data { (try? JSONEncoder().encode(items)) ?? Data("[]".utf8) }

    public var next: Item? { items.first }

    public mutating func add(sessionId: String, action: FinalizeAction) {
        guard !items.contains(where: { $0.sessionId == sessionId }) else { return }
        items.append(Item(sessionId: sessionId, action: action))
    }

    public mutating func remove(sessionId: String) {
        items.removeAll { $0.sessionId == sessionId }
    }
}
