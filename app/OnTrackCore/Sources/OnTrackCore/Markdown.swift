import Foundation

/// Small Markdown → HTML renderer for OnTrack's own notes (summary, transcript, Q&A).
/// Covers what the engine writes: headings, paragraphs, lists (nested by indent), quotes, code,
/// rules, bold/italic, inline code and links. Everything else is shown as escaped text.
public enum Markdown {
    public static func html(_ source: String) -> String {
        var out: [String] = []
        var para: [String] = []
        var listStack: [(tag: String, indent: Int)] = []
        var inCode = false
        var code: [String] = []
        var quote: [String] = []

        func flushPara() {
            guard !para.isEmpty else { return }
            out.append("<p>" + para.map(inline).joined(separator: "<br>") + "</p>")
            para.removeAll()
        }
        func closeLists(to depth: Int = 0) {
            while listStack.count > depth { out.append("</li></\(listStack.removeLast().tag)>") }
        }
        func flushQuote() {
            guard !quote.isEmpty else { return }
            out.append("<blockquote>" + html(quote.joined(separator: "\n")) + "</blockquote>")
            quote.removeAll()
        }
        func flushAll() { flushPara(); closeLists(); flushQuote() }

        for raw in source.replacingOccurrences(of: "\r\n", with: "\n").components(separatedBy: "\n") {
            let line = raw.replacingOccurrences(of: "\t", with: "    ")
            let trimmed = line.trimmingCharacters(in: .whitespaces)

            if inCode {
                if trimmed.hasPrefix("```") {
                    out.append("<pre><code>" + escape(code.joined(separator: "\n")) + "</code></pre>")
                    code.removeAll()
                    inCode = false
                } else {
                    code.append(line)
                }
                continue
            }
            if trimmed.hasPrefix("```") { flushAll(); inCode = true; continue }

            if trimmed.hasPrefix(">") {
                flushPara(); closeLists()
                quote.append(String(trimmed.dropFirst()).trimmingCharacters(in: .whitespaces))
                continue
            }
            flushQuote()

            if trimmed.isEmpty { flushPara(); continue }

            if let (level, text) = heading(trimmed) {
                flushPara(); closeLists()
                out.append("<h\(level)>\(inline(text))</h\(level)>")
                continue
            }
            if isRule(trimmed) {
                flushPara(); closeLists()
                out.append("<hr>")
                continue
            }
            if let (tag, text) = listItem(trimmed) {
                flushPara()
                let indent = line.prefix(while: { $0 == " " }).count
                while let top = listStack.last, top.indent > indent { closeLists(to: listStack.count - 1); _ = top }
                if let top = listStack.last, top.indent == indent {
                    if top.tag == tag {
                        out.append("</li><li>\(inline(text))")
                    } else {
                        closeLists(to: listStack.count - 1)
                        listStack.append((tag, indent))
                        out.append("<\(tag)><li>\(inline(text))")
                    }
                } else {
                    listStack.append((tag, indent))
                    out.append("<\(tag)><li>\(inline(text))")
                }
                continue
            }
            if !listStack.isEmpty, line.hasPrefix("  ") {  // continuation of a list item
                out.append(" " + inline(trimmed))
                continue
            }
            closeLists()
            para.append(trimmed)
        }
        if inCode { out.append("<pre><code>" + escape(code.joined(separator: "\n")) + "</code></pre>") }
        flushAll()
        return out.joined()
    }

    // MARK: blocks

    private static func heading(_ s: String) -> (Int, String)? {
        let hashes = s.prefix(while: { $0 == "#" }).count
        guard (1...6).contains(hashes), s.count > hashes, s[s.index(s.startIndex, offsetBy: hashes)] == " " else {
            return nil
        }
        var text = String(s.dropFirst(hashes + 1)).trimmingCharacters(in: .whitespaces)
        while text.hasSuffix("#") { text.removeLast() }
        return (hashes, text.trimmingCharacters(in: .whitespaces))
    }

    private static func isRule(_ s: String) -> Bool {
        let c = s.replacingOccurrences(of: " ", with: "")
        guard c.count >= 3, let f = c.first, "-*_".contains(f) else { return false }
        return c.allSatisfy { $0 == f }
    }

    private static func listItem(_ s: String) -> (String, String)? {
        for m in ["- ", "* ", "+ "] where s.hasPrefix(m) {
            return ("ul", String(s.dropFirst(2)))
        }
        let digits = s.prefix(while: { $0.isNumber })
        if !digits.isEmpty, digits.count <= 3 {
            let rest = s.dropFirst(digits.count)
            if rest.hasPrefix(". ") || rest.hasPrefix(") ") { return ("ol", String(rest.dropFirst(2))) }
        }
        return nil
    }

    // MARK: inline

    public static func escape(_ s: String) -> String {
        var r = ""
        r.reserveCapacity(s.count)
        for ch in s {
            switch ch {
            case "&": r += "&amp;"
            case "<": r += "&lt;"
            case ">": r += "&gt;"
            case "\"": r += "&quot;"
            default: r.append(ch)
            }
        }
        return r
    }

    /// Inline spans. Code spans are cut out first so their contents are never formatted.
    static func inline(_ s: String) -> String {
        var result = ""
        var rest = Substring(s)
        while let open = rest.firstIndex(of: "`") {
            result += spans(String(rest[..<open]))
            let after = rest.index(after: open)
            if let close = rest[after...].firstIndex(of: "`") {
                let content = String(rest[after..<close])
                let cls = isTimestamp(content) ? " class=\"ts\"" : ""
                result += "<code\(cls)>\(escape(content))</code>"
                rest = rest[rest.index(after: close)...]
            } else {
                result += spans(String(rest[open...]))
                rest = ""
            }
        }
        return result + spans(String(rest))
    }

    private static func isTimestamp(_ s: String) -> Bool {
        let parts = s.split(separator: ":")
        return (2...3).contains(parts.count) && parts.allSatisfy { !$0.isEmpty && $0.allSatisfy(\.isNumber) }
    }

    private static func spans(_ s: String) -> String {
        var t = escape(s)
        t = replace(t, #"\[([^\]]+)\]\((https?://[^)\s]+|file://[^)\s]+)\)"#, #"<a href="$2">$1</a>"#)
        t = replace(t, #"\*\*(.+?)\*\*"#, "<strong>$1</strong>")
        t = replace(t, #"__(.+?)__"#, "<strong>$1</strong>")
        t = replace(t, #"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])"#, "<em>$1</em>")
        t = replace(t, #"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])"#, "<em>$1</em>")
        t = replace(t, #"\[(\d{1,2}:\d{2}(?::\d{2})?)\]"#, #"<span class="ts">[$1]</span>"#)
        return t
    }

    private static func replace(_ s: String, _ pattern: String, _ template: String) -> String {
        guard let re = try? NSRegularExpression(pattern: pattern) else { return s }
        return re.stringByReplacingMatches(in: s, range: NSRange(s.startIndex..., in: s), withTemplate: template)
    }
}
