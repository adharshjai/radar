import SwiftUI

struct MenuBarView: View {
    @Environment(AppState.self) private var state
    @Environment(\.openWindow) private var openWindow
    @Environment(\.openSettings) private var openSettings

    var body: some View {
        let d = state.dashboard
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                StatTile(value: d.stats.overdue, label: "overdue", tint: d.stats.overdue > 0 ? Palette.critical : nil)
                StatTile(value: d.stats.today, label: "today", tint: d.stats.today > 0 ? Palette.warning : nil)
                StatTile(value: d.stats.tomorrow, label: "tomorrow")
                StatTile(value: d.stats.new, label: "new")
            }
            let top = Array((d.buckets.overdue + d.buckets.today + d.buckets.tomorrow + d.buckets.week).prefix(7))
            if top.isEmpty {
                Text("Nothing due soon. 🎉").foregroundStyle(.secondary).padding(.vertical, 6)
            } else {
                VStack(alignment: .leading, spacing: 5) {
                    ForEach(top) { it in
                        HStack(spacing: 8) {
                            Circle().fill(it.course_id.flatMap { state.courseColors[$0] } ?? .gray).frame(width: 7, height: 7)
                            Button { state.open(it) } label: { Text(it.title).lineLimit(1) }.buttonStyle(.plain)
                            Spacer()
                            Text(it.due_text ?? "").font(.caption).foregroundStyle(it.isOverdue && it.status != "graded" ? Palette.critical : .secondary)
                            Button { state.markDone(it) } label: { Image(systemName: "checkmark") }.buttonStyle(.borderless).controlSize(.small).help("Done")
                        }
                    }
                }
            }
            Divider()
            HStack {
                Button("Open Radar") { openWindow(id: "main"); NSApp.activate() }
                Button("Settings…") { openSettings(); NSApp.activate() }
                Button(state.syncing ? "Syncing…" : "Sync now") { state.sync() }.disabled(state.syncing || state.isDemo)
                Spacer()
                Text(state.isDemo ? "demo" : (d.last_sync.map { Fmt.relative($0) } ?? "")).font(.caption).foregroundStyle(.secondary)
                Button("Quit") { NSApp.terminate(nil) }
            }
        }
        .padding(12)
        .frame(width: 420)
    }
}
