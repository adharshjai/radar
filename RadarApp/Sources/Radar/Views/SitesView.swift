import SwiftUI

struct SitesView: View {
    @Environment(AppState.self) private var state
    @State private var sites: [Site] = []
    @State private var loading = true

    private var grouped: [(course: Course?, id: String, sites: [Site])] {
        let byCourse = Dictionary(grouping: sites, by: { $0.course_id ?? "" })
        return byCourse.keys.sorted().map { id in
            (state.dashboard.courses.first(where: { $0.id == id }), id, byCourse[id] ?? [])
        }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                Text("Radar can't see inside these platforms, so it reminds you to open them. Opening a site from here records the visit; set how often you want a reminder.")
                    .font(.callout).foregroundStyle(.secondary)
                if sites.isEmpty && !loading {
                    EmptyHint(symbol: "globe", title: "No external links yet", text: "Sync Canvas and every LabFlow, Gradescope, lab manual and form linked from your courses will appear here.")
                }
                ForEach(grouped, id: \.id) { g in
                    VStack(alignment: .leading, spacing: 6) {
                        HStack(spacing: 8) {
                            Circle().fill(state.courseColors[g.id] ?? .gray).frame(width: 10, height: 10)
                            Text(g.course?.displayName ?? (g.sites.first?.course_name ?? "Course \(g.id)")).font(.headline)
                        }
                        ForEach([("tool", "Tools & platforms"), ("link", "Links"), ("media", "Video / meetings")], id: \.0) { cat, label in
                            let rows = g.sites.filter { $0.category == cat }
                            if !rows.isEmpty {
                                Text(label.uppercased()).font(.caption2.weight(.semibold)).foregroundStyle(.secondary).padding(.top, 4)
                                ForEach(rows) { siteRow($0) }
                            }
                        }
                    }
                    .padding(12)
                    .background(RoundedRectangle(cornerRadius: 12).fill(Color(nsColor: .controlBackgroundColor)))
                    .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color(nsColor: .separatorColor), lineWidth: 1))
                }
            }
            .padding(16)
        }
        .overlay { if loading { ProgressView() } }
        .navigationTitle("Sites & tools")
        .task(id: state.dashboard.generated_at ?? "") { sites = await state.sites(); loading = false }
    }

    private func siteRow(_ s: Site) -> some View {
        HStack(alignment: .top, spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Button { state.openSite(s) } label: { Text(s.name ?? s.url).fontWeight(.medium) }.buttonStyle(.link)
                    if let p = s.platform { PlatformBadge(name: p) }
                }
                Text("\(s.url) · found in \(s.where_found ?? "")").font(.caption).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
            }
            Spacer()
            Text(s.last_checked.map { "opened \(Fmt.relative($0))" } ?? "never opened").font(.caption).foregroundStyle(.secondary).frame(width: 110, alignment: .trailing)
            Picker("", selection: Binding(get: { s.check_every_days }, set: { state.setSiteReminder(s, days: $0) })) {
                Text("no reminder").tag(0); Text("daily").tag(1); Text("every 2 days").tag(2); Text("every 3 days").tag(3); Text("weekly").tag(7); Text("every 2 weeks").tag(14)
            }
            .labelsHidden().frame(width: 130).controlSize(.small)
            Button { state.markSiteChecked(s) } label: { Image(systemName: "checkmark") }.help("Mark checked now").buttonStyle(.borderless)
            Button { state.hideSite(s); sites.removeAll { $0.id == s.id } } label: { Image(systemName: "xmark") }.help("Hide this link").buttonStyle(.borderless)
        }
        .padding(.vertical, 4)
    }
}
