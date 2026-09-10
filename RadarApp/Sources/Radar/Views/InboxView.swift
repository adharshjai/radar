import SwiftUI

struct InboxView: View {
    @Environment(AppState.self) private var state
    @State private var mail: [Item] = []
    @State private var showAll = false
    @State private var loading = true

    var body: some View {
        ScrollView {
            LazyVStack(spacing: 8) {
                if mail.isEmpty && !loading {
                    EmptyHint(symbol: "tray", title: "No mail yet",
                              text: "Connect Outlook (or Apple Mail) in Settings → Email. Canvas announcements and inbox messages also show up here.")
                }
                ForEach(mail) { it in
                    VStack(alignment: .leading, spacing: 2) {
                        ItemRow(item: it)
                        meta(it)
                    }
                    .opacity((it.ai?.relevant == false || it.extra?.bulk == true) ? 0.6 : 1)
                }
            }
            .padding(16)
        }
        .overlay { if loading { ProgressView() } }
        .navigationTitle("Inbox")
        .navigationSubtitle("Outlook · Canvas inbox · announcements")
        .toolbar { ToolbarItem { Toggle("Show everything", isOn: $showAll).toggleStyle(.checkbox) } }
        .task(id: "\(showAll)-\(state.dashboard.generated_at ?? "")") {
            mail = await state.inbox(all: showAll)
            loading = false
        }
    }

    @ViewBuilder private func meta(_ it: Item) -> some View {
        if it.kind == "email" || it.ai != nil {
            HStack(spacing: 6) {
                if let ai = it.ai {
                    if let c = ai.category { Text(c) }
                    if let u = ai.urgency { Text("· urgency \(u)") }
                    if ai.action_required == true { Text("· action").fontWeight(.semibold) }
                } else if let ex = it.extra {
                    if let r = ex.relevance { Text("relevance \(r)") }
                    if let rs = ex.reasons, !rs.isEmpty { Text("· \(rs.joined(separator: ", "))") }
                    if ex.action_required == true { Text("· action?").fontWeight(.semibold) }
                }
                if it.extra?.canvas_notification == true { Text("· Canvas notification") }
                if it.extra?.dup_of != nil { Text("· duplicate") }
                if it.extra?.focused == false { Text("· \"Other\" tab") }
            }
            .font(.caption2).foregroundStyle(.tertiary).padding(.horizontal, 14)
        }
    }
}
