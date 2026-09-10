import SwiftUI

struct CalendarView: View {
    @Environment(AppState.self) private var state
    @State private var days: [CalendarDay] = []
    @State private var span = 21
    @State private var loading = true

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 8) {
                ForEach(days) { day in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack(spacing: 8) {
                            Text(Fmt.dayLong(day.date)).font(day.is_today ? .headline : .subheadline.weight(.semibold))
                                .foregroundStyle(day.is_today ? Color.accentColor : (day.weekend ? .secondary : .primary))
                            if day.is_today { Badge(text: "today", color: .accentColor) }
                            Spacer()
                        }
                        if day.items.isEmpty {
                            Text("—").font(.caption).foregroundStyle(.quaternary)
                        } else {
                            ForEach(day.items) { it in
                                HStack(spacing: 8) {
                                    RoundedRectangle(cornerRadius: 1.5).fill(it.course_id.flatMap { state.courseColors[$0] } ?? .gray).frame(width: 3, height: 18)
                                    Text(it.extra?.all_day == true ? "all day" : Fmt.time(it.whenDate)).font(.caption.monospacedDigit()).foregroundStyle(.secondary).frame(width: 62, alignment: .leading)
                                    Image(systemName: it.symbol).font(.caption).foregroundStyle(.secondary).frame(width: 16)
                                    Button { state.open(it) } label: { Text(it.title).lineLimit(1) }.buttonStyle(.plain).disabled(it.url == nil)
                                    if let c = it.course { Text("· \(c)").font(.caption).foregroundStyle(.secondary).lineLimit(1) }
                                    if let p = it.platform { PlatformBadge(name: p) }
                                    if it.status == "missing" || it.status == "pastdue" { Badge(text: it.status_label ?? it.status, color: Palette.critical, filled: true) }
                                    if let loc = it.extra?.location, !loc.isEmpty { Text("@ \(loc)").font(.caption).foregroundStyle(.secondary) }
                                    Spacer()
                                }
                                .contextMenu {
                                    Button("Mark Done") { state.markDone(it) }
                                    if it.url != nil { Button("Open") { state.open(it) } }
                                }
                            }
                        }
                    }
                    .padding(.horizontal, 12).padding(.vertical, day.items.isEmpty ? 4 : 8)
                    .background(RoundedRectangle(cornerRadius: 10).fill(Color(nsColor: .controlBackgroundColor)).opacity(day.items.isEmpty ? 0.5 : 1))
                    .overlay(RoundedRectangle(cornerRadius: 10).stroke(day.is_today ? Color.accentColor : Color(nsColor: .separatorColor), lineWidth: 1))
                }
            }
            .padding(16)
        }
        .overlay { if loading { ProgressView() } }
        .navigationTitle("Calendar")
        .toolbar {
            ToolbarItem(placement: .automatic) {
                Picker("Range", selection: $span) {
                    Text("2 weeks").tag(14); Text("3 weeks").tag(21); Text("2 months").tag(60); Text("Semester").tag(120)
                }.pickerStyle(.segmented)
            }
        }
        .task(id: "\(span)-\(state.dashboard.generated_at ?? "")") {
            days = await state.calendar(days: span)
            loading = false
        }
    }
}
