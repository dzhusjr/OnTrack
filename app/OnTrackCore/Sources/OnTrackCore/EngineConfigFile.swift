import Foundation

/// Reads and edits top-level `key = value` lines of the engine's config.toml without touching anything else
/// (comments, other keys, tables). Only the value types the engine's settings use: text, true/false, numbers
/// and lists of text.
public struct EngineConfigFile {
    public private(set) var lines: [String]

    public init(text: String) {
        var parts = text.components(separatedBy: "\n")
        if parts.last == "" { parts.removeLast() }
        lines = parts
    }

    public init(url: URL) {
        self.init(text: (try? String(contentsOf: url, encoding: .utf8)) ?? "")
    }

    public var text: String { lines.isEmpty ? "" : lines.joined(separator: "\n") + "\n" }

    public func write(to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try text.write(to: url, atomically: true, encoding: .utf8)
    }

    // MARK: read

    public func string(_ key: String) -> String? {
        guard let raw = rawValue(key) else { return nil }
        return Self.parseString(raw)
    }

    public func bool(_ key: String) -> Bool? {
        switch rawValue(key) {
        case "true": return true
        case "false": return false
        default: return nil
        }
    }

    public func int(_ key: String) -> Int? { rawValue(key).flatMap { Int($0) } }
    public func double(_ key: String) -> Double? { rawValue(key).flatMap { Double($0) } }

    public func strings(_ key: String) -> [String]? {
        guard let raw = rawValue(key), raw.hasPrefix("["), raw.hasSuffix("]") else { return nil }
        var out: [String] = []
        var rest = Substring(raw.dropFirst().dropLast())
        while let start = rest.firstIndex(of: "\"") {
            let (value, after) = Self.readQuoted(rest[start...])
            out.append(value)
            rest = after
        }
        return out
    }

    // MARK: write (nil removes the key, so the engine falls back to its default)

    public mutating func set(_ key: String, string value: String?) {
        setRaw(key, value.map(Self.quote))
    }

    public mutating func set(_ key: String, bool value: Bool?) {
        setRaw(key, value.map { $0 ? "true" : "false" })
    }

    public mutating func set(_ key: String, int value: Int?) {
        setRaw(key, value.map(String.init))
    }

    public mutating func set(_ key: String, double value: Double?) {
        setRaw(key, value.map { String($0) })
    }

    public mutating func set(_ key: String, strings value: [String]?) {
        setRaw(key, value.map { "[" + $0.map(Self.quote).joined(separator: ", ") + "]" })
    }

    // MARK: internals

    /// Index of the first `[table]` header: keys after it belong to that table, not to the top level.
    private var topLevelEnd: Int {
        lines.firstIndex { $0.trimmingCharacters(in: .whitespaces).hasPrefix("[") } ?? lines.count
    }

    private func index(of key: String) -> Int? {
        for i in 0..<topLevelEnd {
            let t = lines[i].trimmingCharacters(in: .whitespaces)
            guard !t.hasPrefix("#"), let eq = t.firstIndex(of: "=") else { continue }
            if t[..<eq].trimmingCharacters(in: .whitespaces) == key { return i }
        }
        return nil
    }

    private func rawValue(_ key: String) -> String? {
        guard let i = index(of: key) else { return nil }
        let t = lines[i].trimmingCharacters(in: .whitespaces)
        let eq = t.firstIndex(of: "=")!
        let (value, _) = Self.splitComment(String(t[t.index(after: eq)...]))
        return value
    }

    private mutating func setRaw(_ key: String, _ raw: String?) {
        if let i = index(of: key) {
            guard let raw else { lines.remove(at: i); return }
            let t = lines[i].trimmingCharacters(in: .whitespaces)
            let eq = t.firstIndex(of: "=")!
            let (_, comment) = Self.splitComment(String(t[t.index(after: eq)...]))
            lines[i] = "\(key) = \(raw)" + (comment.isEmpty ? "" : "  \(comment)")
        } else if let raw {
            lines.insert("\(key) = \(raw)", at: topLevelEnd)
        }
    }

    /// "value  # comment" → ("value", "# comment"); a # inside quotes is part of the value.
    static func splitComment(_ s: String) -> (String, String) {
        var inQuotes = false
        var escaped = false
        for (i, ch) in s.enumerated() {
            if escaped { escaped = false; continue }
            if ch == "\\" && inQuotes { escaped = true; continue }
            if ch == "\"" { inQuotes.toggle(); continue }
            if ch == "#" && !inQuotes {
                let idx = s.index(s.startIndex, offsetBy: i)
                return (s[..<idx].trimmingCharacters(in: .whitespaces), String(s[idx...]))
            }
        }
        return (s.trimmingCharacters(in: .whitespaces), "")
    }

    static func quote(_ s: String) -> String {
        let escaped = s.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"")
        return "\"\(escaped)\""
    }

    static func parseString(_ raw: String) -> String? {
        guard raw.hasPrefix("\"") else { return nil }
        return readQuoted(Substring(raw)).0
    }

    /// Reads one "quoted" string starting at the first character; returns it and what follows.
    private static func readQuoted(_ s: Substring) -> (String, Substring) {
        var out = ""
        var i = s.index(after: s.startIndex)
        while i < s.endIndex {
            let ch = s[i]
            if ch == "\\", s.index(after: i) < s.endIndex {
                i = s.index(after: i)
                switch s[i] {
                case "n": out.append("\n")
                case "t": out.append("\t")
                default: out.append(s[i])
                }
            } else if ch == "\"" {
                return (out, s[s.index(after: i)...])
            } else {
                out.append(ch)
            }
            i = s.index(after: i)
        }
        return (out, s[s.endIndex...])
    }
}
