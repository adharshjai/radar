import SwiftUI

struct TodayView: View {
    @Environment(AppState.self) private var state

    private var d: Dashboard { state.dashboard }
    private var courseFilter: String? { state.courseFilter }
    private func f(_ items: [Item]) -> [Item] { courseFilter == nil ? items : items.filter { $0.course_id == courseFilter } }

    /// Header numbers follow the course filter too.
    private var stats: Stats {
        guard courseFilter != nil else { return d.stats }
        let b = d.buckets
        return Stats(overdue: f(b.overdue).count, today: f(b.today).count, tomorrow: f(b.tomorrow).count, week: f(b.week).count,
                     new: f(d.new_info).count, unread_mail: d.stats.unread_mail, checks: d.stats.checks, tasks: f(b.all).count)
    }
    private var workload: [WorkloadDay] {
        guard courseFilter != nil else { return d.workload }
        let tasks = f(d.buckets.all)
        return d.workload.map { day in
            let due = tasks.filter { t in t.dueDate.map { ISO8601DateFormatter.dateOnly.string(from: $0) == day.date } ?? false }
            return WorkloadDay(date: day.date, label: day.label, count: due.count, titles: due.prefix(6).map(\.title))
        }
    }
    private var agendaDays: [AgendaDay] {
        courseFilter == nil ? d.agenda : d.agenda.map { AgendaDay(date: $0.date, label: $0.label, sub: $0.sub, items: f($0.items)) }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                header
                ForEach(d.problems) { ProblemBanner(problem: $0) }
                filters
                HStack(alignment: .top, spacing: 14) {
                    VStack(spacing: 12) {
                        SectionBox(title: "Overdue / missing", count: f(d.buckets.overdue).count, tint: Palette.critical, emptyText: "Nothing overdue. 🎉") { rows(f(d.buckets.overdue)) }
                        SectionBox(title: "Due today", count: f(d.buckets.today).count, tint: Color(red: 0.66, green: 0.41, blue: 0.04), emptyText: "Nothing due today.") { rows(f(d.buckets.today)) }
                        SectionBox(title: "Due tomorrow", count: f(d.buckets.tomorrow).count, emptyText: "Nothing due tomorrow.") { rows(f(d.buckets.tomorrow)) }
                        SectionBox(title: "This week", count: f(d.buckets.week).count, emptyText: "Nothing else due this week.") { rows(f(d.buckets.week)) }
                        SectionBox(title: "No date yet · sites to check", count: f(d.buckets.nodate).count, emptyText: "Nothing pending without a date.") { rows(f(d.buckets.nodate)) }
                        SectionBox(title: "Later", count: f(d.buckets.later).count, collapsedByDefault: true, emptyText: "Nothing scheduled further out.") { rows(f(d.buckets.later), compact: true) }
                        SectionBox(title: "Snoozed", count: f(d.snoozed).count, collapsedByDefault: true, emptyText: "Nothing snoozed.") { rows(f(d.snoozed), compact: true) }
                        SectionBox(title: "Done recently", count: f(d.done_recent).count, collapsedByDefault: true, emptyText: "Nothing marked done yet.") { rows(f(d.done_recent), compact: true) }
                    }
                    .frame(maxWidth: .infinity)
                    VStack(spacing: 12) {
                        SectionBox(title: "New since your last look", count: f(d.new_info).count, tint: .accentColor, emptyText: "Nothing new. ✅") { rows(f(d.new_info)) }
                        if !d.graded.isEmpty {
                            SectionBox(title: "Recently graded", count: f(d.graded).count) { rows(f(d.graded), compact: true) }
                        }
                        agenda
                    }
                    .frame(minWidth: 320, idealWidth: 400, maxWidth: 440)
                }
            }
            .padding(16)
        }
        .background(Color(nsColor: .windowBackgroundColor))
        .navigationTitle("Today")
        .onAppear { scheduleVisited() }
    }

    private var header: some View {
        FlowLayout(spacing: 10, rowSpacing: 10) {
            let s = stats
            StatTile(value: s.overdue, label: "overdue", tint: s.overdue > 0 ? Palette.critical : nil)
            StatTile(value: s.today, label: "due today", tint: s.today > 0 ? Palette.warning : nil)
            StatTile(value: s.tomorrow, label: "tomorrow")
            StatTile(value: s.week, label: "this week")
            StatTile(value: s.new, label: "new")
            StatTile(value: s.unread_mail, label: "unread mail")
            WorkloadChart(days: workload)
        }
    }

    private var filters: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                chip("All courses", color: nil, selected: courseFilter == nil) { state.courseFilter = nil }
                ForEach(d.courses) { c in
                    chip(c.shortName, color: state.courseColors[c.id], selected: courseFilter == c.id) { state.courseFilter = courseFilter == c.id ? nil : c.id }
                }
            }
        }
    }

    private func chip(_ text: String, color: Color?, selected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 6) {
                if let color { Circle().fill(color).frame(width: 8, height: 8) }
                Text(text)
            }
            .font(.callout)
            .padding(.horizontal, 10).padding(.vertical, 4)
            .background(Capsule().fill(selected ? Color.accentColor.opacity(0.15) : Color(nsColor: .controlBackgroundColor)))
            .overlay(Capsule().stroke(selected ? Color.accentColor : Color(nsColor: .separatorColor), lineWidth: 1))
        }
        .buttonStyle(.plain)
    }

    private func rows(_ items: [Item], compact: Bool = false) -> some View {
        ForEach(items) { ItemRow(item: $0, compact: compact) }
    }

    private var agenda: some View {
        let days = agendaDays.filter { !$0.items.isEmpty }
        let total = days.reduce(0) { $0 + $1.items.count }
        return SectionBox(title: "Next 7 days", count: total, emptyText: "Nothing on the calendar this week.") {
            VStack(alignment: .leading, spacing: 8) {
                ForEach(days) { day in
                    VStack(alignment: .leading, spacing: 3) {
                        HStack(spacing: 6) { Text(day.label).font(.subheadline.weight(.semibold)); Text(day.sub).font(.caption).foregroundStyle(.secondary) }
                        ForEach(day.items) { it in
                            HStack(spacing: 6) {
                                Text(it.extra?.all_day == true ? "all day" : Fmt.time(it.whenDate)).font(.caption.monospacedDigit()).foregroundStyle(.secondary).frame(width: 58, alignment: .leading)
                                Image(systemName: it.symbol).font(.caption).foregroundStyle(.secondary)
                                Button { state.open(it) } label: { Text(it.title).lineLimit(1) }.buttonStyle(.plain).disabled(it.url == nil)
                                if let cid = it.course_id { Circle().fill(state.courseColors[cid] ?? .gray).frame(width: 7, height: 7).help(it.course ?? "") }
                                Spacer()
                            }
                            .font(.callout)
                        }
                    }
                }
                Button("Full calendar →") { state.selection = .calendar }.buttonStyle(.link).font(.caption)
            }
            .padding(.horizontal, 6)
        }
    }

    private func scheduleVisited() {
        Task { try? await Task.sleep(for: .seconds(45)); state.visited() }
    }
}
