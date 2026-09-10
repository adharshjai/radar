import SwiftUI

struct StatusView: View {
    @Environment(AppState.self) private var state
    @State private var info: StatusInfo?
    @State private var brief = ""

    var body: some View {
        ScrollView {
            HStack(alignment: .top, spacing: 14) {
                leftColumn.frame(maxWidth: .infinity)
                SetupBox(info: info).frame(width: 380)
            }
            .padding(16)
        }
        .navigationTitle("Status")
        .task(id: state.dashboard.generated_at ?? "") {
            info = await state.status()
            brief = await state.brief()
        }
    }

    private var leftColumn: some View {
        VStack(alignment: .leading, spacing: 12) {
            GroupBox("Sync history") { SyncHistory(syncs: info?.syncs ?? []) }
            GroupBox("Engine log") {
                ScrollView {
                    Text((info?.log ?? state.syncLog).suffix(60).joined(separator: "\n"))
                        .font(.caption.monospaced()).textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(height: 220)
            }
            GroupBox("Daily brief") {
                ScrollView {
                    Text(brief).font(.caption.monospaced()).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(height: 260)
            }
        }
    }
}

private struct SyncHistory: View {
    let syncs: [SyncLogEntry]
    var body: some View {
        if syncs.isEmpty {
            Text("No syncs yet.").foregroundStyle(.secondary).frame(maxWidth: .infinity, alignment: .leading)
        } else {
            VStack(alignment: .leading, spacing: 4) {
                ForEach(syncs.prefix(20)) { SyncRow(entry: $0) }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

private struct SyncRow: View {
    let entry: SyncLogEntry
    private var countsText: String {
        (entry.counts ?? [:]).map { "\($0.key) \($0.value.text)" }.sorted().joined(separator: " · ")
    }
    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: entry.ok ? "checkmark.circle.fill" : "xmark.octagon.fill")
                .foregroundStyle(entry.ok ? Palette.good : Palette.critical)
            Text(Fmt.relative(entry.finished)).frame(width: 80, alignment: .leading).foregroundStyle(.secondary)
            Text(entry.source).frame(width: 90, alignment: .leading)
            Text(entry.ok ? countsText : entry.message)
                .foregroundStyle(entry.ok ? Color.secondary : Palette.critical).lineLimit(2)
            Spacer()
        }
        .font(.callout)
    }
}

private struct SetupBox: View {
    @Environment(AppState.self) private var state
    let info: StatusInfo?

    var body: some View {
        GroupBox("Setup") {
            VStack(alignment: .leading, spacing: 6) {
                if let c = info?.config { configRows(c) }
                if let n = info?.counts { row("Stored", "\(n["items"] ?? 0) items · \(n["sites"] ?? 0) sites · \(n["courses"] ?? 0) courses") }
                if let p = info?.paths { row("Data folder", p["home"] ?? "") }
                row("Engine", state.engine.launchDescription + " · v" + (info?.engine["version"]?.text ?? "?"))
                buttons.padding(.top, 4)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    @ViewBuilder private func configRows(_ c: ConfigSummary) -> some View {
        let canvasText: String = (c.canvas.base_url?.isEmpty == false) ? "\(c.canvas.base_url!) · \(authText(c.canvas))" : "not configured"
        let outlookText: String = c.outlook.signed_in == true ? (c.outlook.account ?? "signed in") : (c.outlook.enabled == true ? "enabled, not signed in" : "off")
        let mailText: String = c.applemail.enabled == true ? ((c.applemail.account?.isEmpty == false) ? c.applemail.account! : "unified inbox") : "off"
        let aiText: String = c.ai.enabled == true ? ((c.ai.has_key == true ? "on" : "on, no key") + " · " + (c.ai.model ?? "")) : "off"
        row("Canvas", canvasText)
        row("Calendar feed", (c.canvas.ics_url?.isEmpty == false) ? "set" : "–")
        row("Outlook", outlookText)
        row("Apple Mail", mailText)
        row("AI", aiText)
        row("Auto-sync", "every \(c.server.sync_every_minutes ?? 0) min")
    }

    private var buttons: some View {
        HStack {
            Button("Open Settings…") { NSApp.sendAction(Selector(("showSettingsWindow:")), to: nil, from: nil) }
            if let u = state.webURL { Button("Web dashboard") { NSWorkspace.shared.open(u) } }
            if let p = info?.paths["home"] { Button("Show data folder") { NSWorkspace.shared.open(URL(fileURLWithPath: p)) } }
        }
    }

    private func authText(_ c: ConfigSummary.Canvas) -> String {
        if c.has_token == true { return "access token" }
        if c.has_session == true { return "browser session (\(c.session_source ?? "saved"))" }
        if c.auth == "off" { return "API off" }
        return "no login yet"
    }

    private func row(_ k: String, _ v: String) -> some View {
        HStack(alignment: .top) {
            Text(k).foregroundStyle(.secondary).frame(width: 100, alignment: .leading)
            Text(v).textSelection(.enabled)
        }
        .font(.callout)
    }
}
