import Foundation

/// One saved session folder in the notes directory (notes/<id>/{summary,transcript,qa,notes}.md).
public struct NoteSession: Identifiable, Equatable, Sendable {
    public enum Kind: String, CaseIterable, Sendable {
        case summary, transcript, qa, notes

        public var title: String {
            switch self {
            case .summary: return "Summary"
            case .transcript: return "Transcript"
            case .qa: return "Q&A"
            case .notes: return "Notes"
            }
        }

        public var fileName: String { rawValue + ".md" }
    }

    public let id: String
    public let folder: URL
    public let title: String
    public let date: Date?
    public let kinds: [Kind]
    public let modified: Date
    public var sessionId: String = ""  // the recording it came from (folder may have been renamed)

    public func file(_ kind: Kind) -> URL { folder.appendingPathComponent(kind.fileName) }
}

public enum NotesLibrary {
    /// Sessions newest first. Folders without any of the known files are skipped.
    public static func scan(_ dir: URL, fileManager fm: FileManager = .default) -> [NoteSession] {
        guard let entries = try? fm.contentsOfDirectory(at: dir, includingPropertiesForKeys: [.isDirectoryKey],
                                                        options: [.skipsHiddenFiles]) else { return [] }
        var sessions: [NoteSession] = []
        for folder in entries {
            var isDir: ObjCBool = false
            guard fm.fileExists(atPath: folder.path, isDirectory: &isDir), isDir.boolValue else { continue }
            let kinds = NoteSession.Kind.allCases.filter {
                fm.fileExists(atPath: folder.appendingPathComponent($0.fileName).path)
            }
            guard !kinds.isEmpty else { continue }
            let id = folder.lastPathComponent
            let modified = kinds.compactMap {
                (try? fm.attributesOfItem(atPath: folder.appendingPathComponent($0.fileName).path))?[.modificationDate] as? Date
            }.max() ?? .distantPast
            let firstFile = folder.appendingPathComponent(kinds[0].fileName)
            let heading = (try? String(contentsOf: firstFile, encoding: .utf8)).flatMap(firstHeading)
            var s = NoteSession(id: id, folder: folder, title: heading ?? fallbackTitle(id),
                                date: date(fromId: id), kinds: kinds, modified: modified)
            let marker = folder.appendingPathComponent(".session_id")
            s.sessionId = ((try? String(contentsOf: marker, encoding: .utf8)) ?? id)
                .trimmingCharacters(in: .whitespacesAndNewlines)
            sessions.append(s)
        }
        // dated sessions newest first; folders without a date in their name after them, newest edit first
        return sessions.sorted { a, b in
            switch (a.date, b.date) {
            case let (da?, db?): return (da, a.id) > (db, b.id)
            case (.some, nil): return true
            case (nil, .some): return false
            case (nil, nil): return (a.modified, a.id) > (b.modified, b.id)
            }
        }
    }

    /// "2026-10-24-2216-session-22-16" → 2026-10-24 22:16 local time.
    public static func date(fromId id: String) -> Date? {
        let parts = id.split(separator: "-", maxSplits: 4, omittingEmptySubsequences: false)
        guard parts.count >= 4, parts[3].count == 4 else { return nil }
        var c = DateComponents()
        c.year = Int(parts[0]); c.month = Int(parts[1]); c.day = Int(parts[2])
        c.hour = Int(parts[3].prefix(2)); c.minute = Int(parts[3].suffix(2))
        guard c.year != nil, c.month != nil, c.day != nil, c.hour != nil, c.minute != nil else { return nil }
        return Calendar.current.date(from: c)
    }

    static func firstHeading(_ text: String) -> String? {
        for line in text.split(separator: "\n", omittingEmptySubsequences: true).prefix(10) {
            let t = line.trimmingCharacters(in: .whitespaces)
            if t.hasPrefix("# ") { return String(t.dropFirst(2)).trimmingCharacters(in: .whitespaces) }
        }
        return nil
    }

    static func fallbackTitle(_ id: String) -> String {
        let parts = id.split(separator: "-")
        guard parts.count > 4 else { return id }
        return parts.dropFirst(4).joined(separator: " ").capitalized
    }

    /// Case-insensitive match on title, id, or (when `deep`) the files' text.
    public static func filter(_ sessions: [NoteSession], query: String, deep: Bool = true) -> [NoteSession] {
        let q = query.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty else { return sessions }
        return sessions.filter { s in
            if s.title.localizedCaseInsensitiveContains(q) || s.id.localizedCaseInsensitiveContains(q) { return true }
            guard deep else { return false }
            return s.kinds.contains { k in
                ((try? String(contentsOf: s.file(k), encoding: .utf8)) ?? "").localizedCaseInsensitiveContains(q)
            }
        }
    }
}
