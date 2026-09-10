import SwiftUI

struct CourseView: View {
    @Environment(AppState.self) private var state
    let courseID: String
    @State private var detail: CourseDetail?

    var body: some View {
        Group {
            if let d = detail {
                content(d)
            } else {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .navigationTitle(detail?.course.displayName ?? "Course")
        .task(id: "\(courseID)-\(state.dashboard.generated_at ?? "")") { detail = await state.course(courseID) }
    }

    private func content(_ d: CourseDetail) -> some View {
        let c = d.course
        let color = state.courseColors[c.id] ?? .gray
        return ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Circle().fill(color).frame(width: 14, height: 14)
                    Text(c.displayName).font(.title2.weight(.semibold))
                    Text([c.code, c.term].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · ")).foregroundStyle(.secondary)
                    if let s = c.current_score {
                        Text(String(format: "%.1f%%", s) + (c.current_grade.map { " (\($0))" } ?? "")).font(.headline)
                    }
                    if let u = c.url.flatMap({ URL(string: $0) }) { Link("Open in Canvas ↗", destination: u).font(.callout) }
                    Spacer()
                }
                if let t = c.teachers, !t.isEmpty { Text("Taught by \(t.joined(separator: ", "))").foregroundStyle(.secondary) }
                HStack(alignment: .top, spacing: 14) {
                    VStack(spacing: 12) {
                        box("Open tasks", d.groups["tasks"] ?? [], empty: "No open tasks in this course.")
                        box("Dates from the syllabus", d.groups["syllabus"] ?? [], empty: "No dated lines found in the syllabus (or none posted).", compact: true)
                        box("Announcements", d.groups["announcements"] ?? [], tint: .accentColor, empty: "No recent announcements.")
                        box("Events, discussions, module requirements", d.groups["other"] ?? [], empty: "Nothing else.", collapsed: true, compact: true)
                        box("Submitted / graded", d.groups["graded"] ?? [], empty: "Nothing graded yet.", collapsed: true, compact: true)
                        box("Recent files", d.groups["files"] ?? [], empty: "No recent files.", collapsed: true, compact: true)
                    }
                    .frame(maxWidth: .infinity)
                    VStack(spacing: 12) {
                        syllabusDigest(d)
                        sitesBox(d.sites)
                    }
                    .frame(width: 380)
                }
            }
            .padding(16)
        }
    }

    private func box(_ title: String, _ items: [Item], tint: Color? = nil, empty: String, collapsed: Bool = false, compact: Bool = false) -> some View {
        SectionBox(title: title, count: items.count, tint: tint, collapsedByDefault: collapsed, emptyText: empty) {
            ForEach(items) { ItemRow(item: $0, showCourse: false, compact: compact) }
        }
    }

    private func syllabusDigest(_ d: CourseDetail) -> some View {
        SectionBox(title: "Syllabus digest", count: (d.syllabus_ai?.exams?.count ?? 0) + (d.syllabus_ai?.deadlines?.count ?? 0), emptyText: "") {
            VStack(alignment: .leading, spacing: 8) {
                if let ai = d.syllabus_ai {
                    if let w = ai.weekly_schedule, !w.isEmpty {
                        Text("Weekly schedule").font(.subheadline.weight(.semibold))
                        ForEach(w) { Text("**\($0.what)** · \($0.days)\($0.time.map { " \($0)" } ?? "")\($0.location.map { " · \($0)" } ?? "")").font(.callout) }
                    }
                    if let e = ai.exams, !e.isEmpty {
                        Text("Exams").font(.subheadline.weight(.semibold)).padding(.top, 4)
                        ForEach(e) { Text("**\($0.name)** · \($0.date ?? "date TBA")\($0.time.map { " \($0)" } ?? "")\($0.location.map { " · \($0)" } ?? "")").font(.callout) }
                    }
                    if let p = ai.policies {
                        Text("Policies").font(.subheadline.weight(.semibold)).padding(.top, 4)
                        if let x = p.late_work { Text("**Late work:** \(x)").font(.callout) }
                        if let x = p.attendance { Text("**Attendance:** \(x)").font(.callout) }
                        if let x = p.grading { Text("**Grading:** \(x)").font(.callout) }
                        if let x = p.contact { Text("**Contact:** \(x)").font(.callout) }
                    }
                    if let k = ai.key_facts, !k.isEmpty {
                        Text("Key facts").font(.subheadline.weight(.semibold)).padding(.top, 4)
                        ForEach(k, id: \.self) { Text("• \($0)").font(.callout) }
                    }
                } else if let t = d.syllabus_text_preview, !t.isEmpty {
                    Text("AI extraction is off, so only dated lines are pulled out (left). Add an Anthropic key in Settings → AI for exams, policies and the weekly schedule.")
                        .font(.callout).foregroundStyle(.secondary)
                    DisclosureGroup("Raw syllabus text") {
                        ScrollView { Text(t).font(.caption.monospaced()).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading) }.frame(maxHeight: 260)
                    }.font(.callout)
                } else {
                    Text("No syllabus page or file found for this course.").font(.callout).foregroundStyle(.secondary)
                }
                if let u = d.course.url.flatMap({ URL(string: "\($0)/assignments/syllabus") }) { Link("Syllabus in Canvas ↗", destination: u).font(.caption) }
            }
            .padding(.horizontal, 6)
        }
    }

    private func sitesBox(_ sites: [Site]) -> some View {
        SectionBox(title: "Sites & tools", count: sites.count, emptyText: "None found.") {
            VStack(alignment: .leading, spacing: 6) {
                ForEach(sites) { s in
                    VStack(alignment: .leading, spacing: 1) {
                        HStack(spacing: 6) {
                            Button { state.openSite(s) } label: { Text(s.name ?? s.url) }.buttonStyle(.link)
                            if let p = s.platform { PlatformBadge(name: p) }
                        }
                        Text((s.where_found ?? "") + (s.last_checked.map { " · opened \(Fmt.relative($0))" } ?? "")).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                    }
                }
            }
            .padding(.horizontal, 6)
        }
    }
}
