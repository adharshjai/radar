import SwiftUI

struct SearchView: View {
    @Environment(AppState.self) private var state
    @State private var items: [Item] = []
    @State private var kinds: [String] = []
    @State private var kind = ""

    var body: some View {
        ScrollView {
            LazyVStack(spacing: 6) {
                if items.isEmpty { EmptyHint(symbol: "magnifyingglass", title: "Nothing matches", text: "Search titles, message bodies and course names with the field in the toolbar.") }
                ForEach(items) { ItemRow(item: $0, compact: true) }
            }
            .padding(16)
        }
        .navigationTitle("Everything")
        .navigationSubtitle("\(items.count) shown")
        .toolbar {
            ToolbarItem {
                Picker("Kind", selection: $kind) {
                    Text("All kinds").tag("")
                    ForEach(kinds, id: \.self) { Text(Kind.label($0)).tag($0) }
                }
            }
        }
        .task(id: "\(state.searchText)|\(kind)|\(state.dashboard.generated_at ?? "")") {
            try? await Task.sleep(for: .milliseconds(200))
            let (i, k) = await state.search(state.searchText, kind: kind)
            items = i
            kinds = k
        }
    }
}
