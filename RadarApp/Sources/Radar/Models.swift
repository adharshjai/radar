import Foundation

// Wire models for the engine's JSON API. Dates arrive as ISO-8601 strings with offsets.

struct ActionItem: Codable, Hashable, Identifiable {
    var id: String { text + (due ?? "") }
    let text: String
    let due: String?
}

struct AIInfo: Codable, Hashable {
    let summary: String?
    let action_items: [ActionItem]?
    let category: String?
    let urgency: Int?
    let relevant: Bool?
    let action_required: Bool?
}

struct ItemExtra: Codable, Hashable {
    let all_day: Bool?
    let hint: String?
    let location: String?
    let late: Bool?
    let soft_due: Bool?
    let dup_of: String?
    let from: String?
    let site_ids: [Int]?
    let action_required: Bool?
    let relevance: Int?
    let mentioned_dates: [String]?
    let canvas_notification: Bool?
    let bulk: Bool?
    let external_url: String?
    let submission_unknown: Bool?
    let requirement: String?
    let module: String?
    let reasons: [String]?
    let action_words: [String]?
    let focused: Bool?
    let flagged: Bool?
}

struct Item: Codable, Hashable, Identifiable {
    var id: String { uid }
    let uid: String
    let source: String
    let kind: String
    let kind_label: String?
    let title: String
    let course: String?
    let course_id: String?
    let url: String?
    let platform: String?
    let status: String
    let status_label: String?
    let points: Double?
    let score: Double?
    let grade: String?
    let sender: String?
    let priority: Int?
    let due_text: String?
    let ago: String?
    let is_new: Bool?
    let is_updated: Bool?
    let user_state: String?
    let note: String?
    let graded_ago: String?
    let due_at: String?
    let starts_at: String?
    let ends_at: String?
    let posted_at: String?
    let prev_due_at: String?
    let snooze_until: String?
    let body: String?
    let extra: ItemExtra?
    let ai: AIInfo?

    var dueDate: Date? { Item.parse(due_at) }
    var startDate: Date? { Item.parse(starts_at) }
    var whenDate: Date? { dueDate ?? startDate }
    var isOverdue: Bool {
        guard let d = dueDate else { return false }
        if extra?.all_day == true, Calendar.current.isDateInToday(d) { return false }
        return d < Date()
    }
    var openURL: URL? { url.flatMap { URL(string: $0) } }
    var symbol: String { Kind.symbol(kind) }
    var isTaskLike: Bool { ["assignment", "quiz", "discussion", "planner_note", "page", "module_item", "syllabus_event", "email", "site_check", "manual"].contains(kind) }

    static let iso: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
    static let isoPlain: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()
    static func parse(_ s: String?) -> Date? {
        guard let s, !s.isEmpty else { return nil }
        return iso.date(from: s) ?? isoPlain.date(from: s)
    }
}

enum Kind {
    static func symbol(_ kind: String) -> String {
        switch kind {
        case "assignment": return "doc.text"
        case "quiz": return "questionmark.circle"
        case "discussion": return "bubble.left.and.bubble.right"
        case "announcement": return "megaphone"
        case "event", "calendar": return "calendar"
        case "module": return "lock.open"
        case "module_item": return "checklist"
        case "planner_note": return "note.text"
        case "page": return "doc.richtext"
        case "conversation": return "envelope"
        case "email": return "envelope.badge"
        case "file": return "paperclip"
        case "syllabus_event": return "book.closed"
        case "site_check": return "globe"
        case "manual": return "star"
        default: return "circle"
        }
    }
    static func label(_ kind: String) -> String {
        switch kind {
        case "assignment": return "Assignment"
        case "quiz": return "Quiz"
        case "discussion": return "Discussion"
        case "announcement": return "Announcement"
        case "event": return "Canvas event"
        case "calendar": return "Outlook calendar"
        case "module": return "Module"
        case "module_item": return "Module requirement"
        case "planner_note": return "Planner note"
        case "page": return "Page"
        case "conversation": return "Canvas inbox"
        case "email": return "Email"
        case "file": return "New file"
        case "syllabus_event": return "From syllabus"
        case "site_check": return "Check site"
        case "manual": return "My task"
        default: return kind
        }
    }
}

struct Course: Codable, Hashable, Identifiable {
    let id: String
    let name: String?
    let code: String?
    let term: String?
    let url: String?
    let teachers: [String]?
    let current_score: Double?
    let current_grade: String?
    let has_syllabus: Bool?
    let has_syllabus_ai: Bool?
    let open_count: Int?
    var displayName: String { name ?? "Course \(id)" }
    var shortName: String { (code?.isEmpty == false ? code! : displayName) }
}

struct Site: Codable, Hashable, Identifiable {
    let id: Int
    let course_id: String?
    let course_name: String?
    let url: String
    let name: String?
    let platform: String?
    let category: String?
    let where_found: String?
    let last_checked: String?
    let check_every_days: Int
    var openURL: URL? { URL(string: url) }
}

struct AgendaDay: Codable, Hashable, Identifiable {
    var id: String { date }
    let date: String
    let label: String
    let sub: String
    let items: [Item]
}

struct WorkloadDay: Codable, Hashable, Identifiable {
    var id: String { date }
    let date: String
    let label: String
    let count: Int
    let titles: [String]
}

struct Stats: Codable, Hashable {
    let overdue: Int
    let today: Int
    let tomorrow: Int
    let week: Int
    let new: Int
    let unread_mail: Int
    let checks: Int
    let tasks: Int
    static let empty = Stats(overdue: 0, today: 0, tomorrow: 0, week: 0, new: 0, unread_mail: 0, checks: 0, tasks: 0)
}

struct Problem: Codable, Hashable, Identifiable {
    var id: String { source + (when ?? "") }
    let source: String
    let message: String
    let fix: String?
    let when: String?
}

struct Buckets: Codable, Hashable {
    let overdue: [Item]
    let today: [Item]
    let tomorrow: [Item]
    let week: [Item]
    let later: [Item]
    let nodate: [Item]
    static let empty = Buckets(overdue: [], today: [], tomorrow: [], week: [], later: [], nodate: [])
    var all: [Item] { overdue + today + tomorrow + week + later + nodate }
}

struct Dashboard: Codable, Hashable {
    let generated_at: String?
    let last_sync: String?
    let syncing: Bool
    let configured: Bool
    let demo: Bool?
    let stats: Stats
    let buckets: Buckets
    let new_info: [Item]
    let graded: [Item]
    let snoozed: [Item]
    let done_recent: [Item]
    let agenda: [AgendaDay]
    let workload: [WorkloadDay]
    let courses: [Course]
    let problems: [Problem]
    static let empty = Dashboard(generated_at: nil, last_sync: nil, syncing: false, configured: false, demo: false, stats: .empty,
                                 buckets: .empty, new_info: [], graded: [], snoozed: [], done_recent: [], agenda: [], workload: [],
                                 courses: [], problems: [])
}

struct CalendarDay: Codable, Hashable, Identifiable {
    var id: String { date }
    let date: String
    let is_today: Bool
    let weekend: Bool
    let items: [Item]
}

struct CourseDetail: Codable, Hashable {
    let course: Course
    let syllabus_ai: SyllabusAI?
    let syllabus_text_preview: String?
    let groups: [String: [Item]]
    let sites: [Site]
}

struct SyllabusAI: Codable, Hashable {
    struct Exam: Codable, Hashable, Identifiable { var id: String { name + (date ?? "") }; let name: String; let date: String?; let time: String?; let location: String?; let notes: String? }
    struct Deadline: Codable, Hashable, Identifiable { var id: String { title + (date ?? "") }; let title: String; let date: String?; let time: String?; let notes: String? }
    struct Weekly: Codable, Hashable, Identifiable { var id: String { what + days }; let what: String; let days: String; let time: String?; let location: String? }
    struct Policies: Codable, Hashable { let late_work: String?; let attendance: String?; let grading: String?; let contact: String? }
    let exams: [Exam]?
    let deadlines: [Deadline]?
    let weekly_schedule: [Weekly]?
    let policies: Policies?
    let key_facts: [String]?
}

struct SyncLogEntry: Codable, Hashable, Identifiable {
    var id: String { source + (finished ?? "") + message }
    let source: String
    let ok: Bool
    let message: String
    let finished: String?
    let counts: [String: JSONValue]?
}

struct StatusInfo: Codable, Hashable {
    let syncs: [SyncLogEntry]
    let log: [String]
    let syncing: Bool
    let config: ConfigSummary
    let paths: [String: String]
    let engine: [String: JSONValue]
    let counts: [String: Int]
}

struct ConfigSummary: Codable, Hashable {
    struct Canvas: Codable, Hashable {
        var base_url: String?; var auth: String?; var browser: String?; var cookie_file: String?; var ics_url: String?
        var announcement_feeds: [String]?; var include_courses: [String]?; var exclude_courses: [String]?
        var lookback_days: Int?; var lookahead_days: Int?; var fetch_files: Bool?; var parse_syllabus_files: Bool?
        var has_token: Bool?; var has_session: Bool?; var session_source: String?; var session_saved_at: String?
    }
    struct Outlook: Codable, Hashable {
        var enabled: Bool?; var client_id: String?; var tenant: String?; var lookback_days: Int?; var calendar: Bool?
        var folders: [String]?; var max_messages: Int?; var signed_in: Bool?; var account: String?
    }
    struct AppleMail: Codable, Hashable { var enabled: Bool?; var account: String?; var lookback_days: Int?; var max_messages: Int? }
    struct AI: Codable, Hashable { var enabled: Bool?; var model: String?; var max_items_per_sync: Int?; var has_key: Bool?; var key_in_config: Bool? }
    struct Profile: Codable, Hashable { var name: String?; var email: String?; var university_domains: [String]?; var extra_keywords: [String]? }
    struct Server: Codable, Hashable { var sync_every_minutes: Int?; var notify: Bool?; var port: Int? }
    var canvas: Canvas
    var outlook: Outlook
    var applemail: AppleMail
    var ai: AI
    var profile: Profile
    var server: Server
}

/// Minimal JSON value for loosely typed engine fields.
enum JSONValue: Codable, Hashable {
    case string(String), number(Double), bool(Bool), null, array([JSONValue]), object([String: JSONValue])
    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let b = try? c.decode(Bool.self) { self = .bool(b) }
        else if let n = try? c.decode(Double.self) { self = .number(n) }
        else if let s = try? c.decode(String.self) { self = .string(s) }
        else if let a = try? c.decode([JSONValue].self) { self = .array(a) }
        else { self = .object(try c.decode([String: JSONValue].self)) }
    }
    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .string(let s): try c.encode(s)
        case .number(let n): try c.encode(n)
        case .bool(let b): try c.encode(b)
        case .null: try c.encodeNil()
        case .array(let a): try c.encode(a)
        case .object(let o): try c.encode(o)
        }
    }
    var text: String {
        switch self {
        case .string(let s): return s
        case .number(let n): return n == n.rounded() ? String(Int(n)) : String(n)
        case .bool(let b): return b ? "yes" : "no"
        case .null: return ""
        case .array(let a): return a.map(\.text).joined(separator: ", ")
        case .object(let o): return o.map { "\($0.key)=\($0.value.text)" }.sorted().joined(separator: " ")
        }
    }
}
