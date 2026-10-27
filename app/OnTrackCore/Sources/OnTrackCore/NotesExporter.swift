import Foundation

/// Copies a saved session's Markdown files (transcript, summary, Q&A, your notes) to a folder you choose.
public enum NotesExporter {
    public struct MissingNotes: Error, Equatable {
        public let path: String
    }

    /// Copies `*.md` from `notesDir` into `parent/<session id>/`, replacing older copies. Returns the destination.
    @discardableResult
    /// "<project>/Meetings/2026-10-27": where a session is also saved inside the work folder.
    public static func meetingsFolder(project: URL, date: Date, calendar: Calendar = .current) -> URL {
        let c = calendar.dateComponents([.year, .month, .day], from: date)
        let day = String(format: "%04d-%02d-%02d", c.year ?? 0, c.month ?? 0, c.day ?? 0)
        return project.appendingPathComponent("Meetings", isDirectory: true).appendingPathComponent(day, isDirectory: true)
    }

    public static func export(notesDir: URL, into parent: URL, fileManager fm: FileManager = .default) throws -> URL {
        var isDir: ObjCBool = false
        guard fm.fileExists(atPath: notesDir.path, isDirectory: &isDir), isDir.boolValue else {
            throw MissingNotes(path: notesDir.path)
        }
        let dest = parent.appendingPathComponent(notesDir.lastPathComponent, isDirectory: true)
        try fm.createDirectory(at: dest, withIntermediateDirectories: true)
        for name in try fm.contentsOfDirectory(atPath: notesDir.path) where name.hasSuffix(".md") {
            let target = dest.appendingPathComponent(name)
            if fm.fileExists(atPath: target.path) { try fm.removeItem(at: target) }
            try fm.copyItem(at: notesDir.appendingPathComponent(name), to: target)
        }
        return dest
    }
}
