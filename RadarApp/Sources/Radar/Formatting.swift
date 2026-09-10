import SwiftUI

enum Palette {
    /// Fixed categorical order (never cycled): course i gets slot i; extras fall back to gray.
    static let series: [Color] = [
        Color(red: 0.165, green: 0.471, blue: 0.839), Color(red: 0.922, green: 0.408, blue: 0.204),
        Color(red: 0.106, green: 0.686, blue: 0.478), Color(red: 0.929, green: 0.631, blue: 0.0),
        Color(red: 0.910, green: 0.482, blue: 0.643), Color(red: 0.0, green: 0.514, blue: 0.0),
        Color(red: 0.290, green: 0.227, blue: 0.655), Color(red: 0.890, green: 0.286, blue: 0.282),
    ]
    static let critical = Color(red: 0.816, green: 0.231, blue: 0.231)
    static let warning = Color(red: 0.980, green: 0.698, blue: 0.098)
    static let good = Color(red: 0.047, green: 0.639, blue: 0.047)
    static let exam = Color(red: 0.290, green: 0.227, blue: 0.655)

    static func courseColors(_ courses: [Course]) -> [String: Color] {
        var out: [String: Color] = [:]
        for (i, c) in courses.sorted(by: { $0.id < $1.id }).enumerated() {
            out[c.id] = i < series.count ? series[i] : .gray
        }
        return out
    }
}

enum Fmt {
    static func time(_ d: Date?) -> String {
        guard let d else { return "" }
        return d.formatted(date: .omitted, time: .shortened)
    }
    static func dayLong(_ s: String) -> String {
        guard let d = ISO8601DateFormatter.dateOnly.date(from: s) else { return s }
        return d.formatted(.dateTime.weekday(.wide).month(.abbreviated).day())
    }
    static func dayShort(_ s: String) -> String {
        guard let d = ISO8601DateFormatter.dateOnly.date(from: s) else { return s }
        return d.formatted(.dateTime.weekday(.abbreviated).month(.abbreviated).day())
    }
    static func relative(_ iso: String?) -> String {
        guard let d = Item.parse(iso) else { return "never" }
        let f = RelativeDateTimeFormatter()
        f.unitsStyle = .short
        return f.localizedString(for: d, relativeTo: Date())
    }
    static func points(_ p: Double?) -> String? {
        guard let p else { return nil }
        return p == p.rounded() ? "\(Int(p)) pts" : String(format: "%.1f pts", p)
    }
    static func score(_ s: Double?, _ p: Double?) -> String? {
        guard let s, let p, p > 0 else { return nil }
        let f: (Double) -> String = { $0 == $0.rounded() ? "\(Int($0))" : String(format: "%.1f", $0) }
        return "\(f(s))/\(f(p))"
    }
}

extension ISO8601DateFormatter {
    static let dateOnly: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withFullDate]
        f.timeZone = .current
        return f
    }()
}
