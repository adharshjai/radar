import SwiftUI

struct SettingsView: View {
    var body: some View {
        TabView {
            CanvasSettings().tabItem { Label("Canvas", systemImage: "graduationcap") }
            EmailSettings().tabItem { Label("Email", systemImage: "envelope") }
            AISettings().tabItem { Label("AI", systemImage: "sparkles") }
            GeneralSettings().tabItem { Label("General", systemImage: "gearshape") }
        }
        .frame(width: 620, height: 520)
    }
}

struct CanvasSettings: View {
    @Environment(AppState.self) private var state
    @State private var baseURL = ""
    @State private var method = "browser"
    @State private var browser = ""
    @State private var token = ""
    @State private var cookie = ""
    @State private var icsURL = ""
    @State private var feeds = ""
    @State private var busy = false
    @State private var result: AppState.LoginResult?
    @State private var loaded = false

    var body: some View {
        Form {
            Section("Your Canvas") {
                TextField("Canvas URL", text: $baseURL, prompt: Text("https://canvas.yourschool.edu"))
                if let c = state.config?.canvas {
                    LabeledContent("Status") {
                        if c.has_token == true { Text("Connected with an access token") }
                        else if c.has_session == true { Text("Browser session from \(c.session_source ?? "browser") · saved \(Fmt.relative(c.session_saved_at))") }
                        else if c.auth == "off" { Text("Canvas API access is off").foregroundStyle(.secondary) }
                        else { Text("Not connected").foregroundStyle(.secondary) }
                    }
                }
            }
            Section("Sign in") {
                Picker("Method", selection: $method) {
                    Text("Reuse my browser's login (recommended)").tag("browser")
                    Text("Personal access token").tag("token")
                    Text("Paste the session cookie").tag("paste")
                }
                .pickerStyle(.radioGroup)
                switch method {
                case "browser":
                    Picker("Browser", selection: $browser) {
                        Text("Try them all").tag(""); Text("Chrome").tag("chrome"); Text("Firefox").tag("firefox"); Text("Safari").tag("safari")
                        Text("Edge").tag("edge"); Text("Brave").tag("brave")
                    }
                    Text("Works even when your school hides \"New Access Token\". Stay logged in to Canvas in that browser; macOS may ask once to allow Keychain access (click Always Allow). Safari needs Full Disk Access for Radar.")
                        .font(.caption).foregroundStyle(.secondary)
                case "token":
                    SecureField("Access token", text: $token)
                    Text("Canvas → Account → Settings → Approved Integrations → + New Access Token. Student tokens now expire after 30 days.").font(.caption).foregroundStyle(.secondary)
                default:
                    SecureField("canvas_session cookie value (or the whole Cookie header)", text: $cookie)
                    Text("Chrome: ⌥⌘I → Application → Cookies → your Canvas site. Safari: Develop → Web Inspector → Storage.").font(.caption).foregroundStyle(.secondary)
                }
                HStack {
                    Button(busy ? "Connecting…" : "Connect") { connect() }.disabled(busy || baseURL.isEmpty).buttonStyle(.borderedProminent)
                    if state.config?.canvas.has_session == true || state.config?.canvas.has_token == true {
                        Button("Disconnect") { Task { await state.canvasLogout(); await state.loadConfig() } }
                    }
                    if let r = result {
                        Image(systemName: r.ok ? "checkmark.circle.fill" : "xmark.octagon.fill").foregroundStyle(r.ok ? Palette.good : Palette.critical)
                        Text(r.message).font(.callout).foregroundStyle(r.ok ? .primary : Palette.critical).textSelection(.enabled).lineLimit(4)
                    }
                }
            }
            Section("Token-free feeds (backup, optional)") {
                TextField("Calendar feed (.ics)", text: $icsURL, prompt: Text("Canvas → Calendar → Calendar Feed"))
                TextField("Announcement feeds (.atom), comma separated", text: $feeds, prompt: Text("Course → Announcements → External Feeds → RSS"))
                Text("Feeds need no login and never expire, but can't see grades or submission status. Radar uses them alongside the main connection.")
                    .font(.caption).foregroundStyle(.secondary)
                Button("Save feeds") { Task { _ = await state.saveConfig(["canvas": ["ics_url": icsURL, "announcement_feeds": feeds]]) } }
            }
        }
        .formStyle(.grouped)
        .task {
            await state.loadConfig()
            if let c = state.config?.canvas, !loaded {
                baseURL = c.base_url ?? ""; browser = c.browser ?? ""; icsURL = c.ics_url ?? ""
                feeds = (c.announcement_feeds ?? []).joined(separator: ", ")
                if c.has_token == true { method = "token" }
                loaded = true
            }
        }
    }

    private func connect() {
        busy = true
        result = nil
        Task {
            result = await state.canvasLogin(baseURL: baseURL, method: method, browser: browser.isEmpty ? nil : browser,
                                             token: method == "token" ? token : nil, cookie: method == "paste" ? cookie : nil)
            busy = false
            if result?.ok == true { token = ""; cookie = ""; state.sync() }
        }
    }
}

struct EmailSettings: View {
    @Environment(AppState.self) private var state
    @State private var code: AppState.DeviceCode?
    @State private var pending = false
    @State private var message = ""
    @State private var appleMail = false
    @State private var appleAccount = ""
    @State private var clientID = ""
    @State private var tenant = "organizations"
    @State private var loaded = false

    var body: some View {
        Form {
            Section("Outlook (Microsoft 365)") {
                if let o = state.config?.outlook, o.signed_in == true {
                    LabeledContent("Signed in as") { Text(o.account ?? "your school account") }
                    Button("Sign out") { Task { await state.outlookLogout() } }
                } else if let c = code {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("1. Copy this code:").font(.callout)
                        HStack {
                            Text(c.code).font(.system(size: 26, weight: .bold, design: .monospaced)).textSelection(.enabled)
                            Button("Copy") { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(c.code, forType: .string) }
                        }
                        Text("2. Sign in with your school account and paste the code:").font(.callout)
                        Button("Open Microsoft sign-in") { if let u = URL(string: c.url) { NSWorkspace.shared.open(u) } }.buttonStyle(.borderedProminent)
                        HStack { if pending { ProgressView().controlSize(.small) }; Text(pending ? "Waiting for you to finish signing in…" : message).font(.callout).foregroundStyle(.secondary) }
                    }
                } else {
                    Button("Sign in with Microsoft") { start() }.buttonStyle(.borderedProminent)
                    if !message.isEmpty { Text(message).font(.callout).foregroundStyle(Palette.critical).textSelection(.enabled) }
                    Text("Read-only access to mail and calendar (Mail.Read, Calendars.Read). If your school blocks the sign-in, ask IT to allow \"Microsoft Graph Command Line Tools\" or use Apple Mail below.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                DisclosureGroup("Advanced") {
                    TextField("Client ID", text: $clientID)
                    TextField("Tenant", text: $tenant)
                    Button("Save") { Task { _ = await state.saveConfig(["outlook": ["client_id": clientID, "tenant": tenant]]) } }
                }
            }
            Section("Apple Mail (fallback)") {
                Toggle("Read the Outlook account already set up in Mail.app", isOn: $appleMail)
                TextField("Account name in Mail.app (blank = unified inbox)", text: $appleAccount).disabled(!appleMail)
                Button("Save") { Task { _ = await state.saveConfig(["applemail": ["enabled": appleMail, "account": appleAccount]]) } }
            }
        }
        .formStyle(.grouped)
        .task {
            await state.loadConfig()
            if let c = state.config, !loaded {
                appleMail = c.applemail.enabled ?? false; appleAccount = c.applemail.account ?? ""
                clientID = c.outlook.client_id ?? ""; tenant = c.outlook.tenant ?? "organizations"
                loaded = true
            }
        }
    }

    private func start() {
        message = ""
        Task {
            switch await state.outlookLoginStart() {
            case .success(let c):
                code = c
                pending = true
                while pending {
                    try? await Task.sleep(for: .seconds(2))
                    let s = await state.outlookLoginStatus()
                    if s.state == "done" { pending = false; code = nil; message = "Connected"; await state.loadConfig(); state.sync() }
                    else if s.state == "error" { pending = false; code = nil; message = s.error ?? "Sign-in failed" }
                }
            case .failure(let e):
                message = e.message
            }
        }
    }
}

struct AISettings: View {
    @Environment(AppState.self) private var state
    @State private var enabled = true
    @State private var model = "claude-opus-5"
    @State private var key = ""
    @State private var loaded = false
    @State private var saved = false

    var body: some View {
        Form {
            Section("Claude enrichment") {
                Toggle("Read new mail, announcements and syllabi with Claude", isOn: $enabled)
                Text("Extracts action items and deadlines from email, and exams, weekly schedule and policies from each syllabus. Results are cached, so re-syncs cost nothing.")
                    .font(.caption).foregroundStyle(.secondary)
                SecureField("Anthropic API key", text: $key, prompt: Text(state.config?.ai.key_in_config == true ? "•••••••• (saved)" : "sk-ant-…"))
                if state.config?.ai.has_key == true && state.config?.ai.key_in_config != true { Text("A key was found in your environment.").font(.caption).foregroundStyle(.secondary) }
                TextField("Model", text: $model)
                HStack {
                    Button("Save") {
                        var patch: [String: Any] = ["enabled": enabled, "model": model]
                        if !key.isEmpty { patch["api_key"] = key }
                        Task { saved = await state.saveConfig(["ai": patch]); key = "" }
                    }.buttonStyle(.borderedProminent)
                    if saved { Label("Saved", systemImage: "checkmark").foregroundStyle(Palette.good) }
                }
            }
        }
        .formStyle(.grouped)
        .task {
            await state.loadConfig()
            if let a = state.config?.ai, !loaded { enabled = a.enabled ?? true; model = a.model ?? "claude-opus-5"; loaded = true }
        }
    }
}

struct GeneralSettings: View {
    @Environment(AppState.self) private var state
    @State private var interval = 30
    @State private var loaded = false

    var body: some View {
        @Bindable var state = state
        Form {
            Section("Sync") {
                Picker("Check for changes", selection: $interval) {
                    Text("Every 15 minutes").tag(15); Text("Every 30 minutes").tag(30); Text("Every hour").tag(60); Text("Every 3 hours").tag(180); Text("Only when I press Sync").tag(0)
                }
                .onChange(of: interval) { _, v in if loaded { Task { _ = await state.saveConfig(["server": ["sync_every_minutes": v]]) } } }
                Text("Changes to the interval apply the next time Radar starts.").font(.caption).foregroundStyle(.secondary)
            }
            Section("Notifications") {
                Toggle("Notify me about new overdue, due-today and action items", isOn: Binding(get: { state.notificationsEnabled }, set: { state.notificationsEnabled = $0 }))
            }
            Section("Startup") {
                Toggle("Open Radar at login", isOn: $state.launchAtLogin)
                Text("Radar keeps syncing quietly from the menu bar while the window is closed.").font(.caption).foregroundStyle(.secondary)
            }
            Section("More") {
                if let u = state.webURL { Button("Open the web dashboard") { NSWorkspace.shared.open(u) } }
                Text("Engine: \(state.engine.launchDescription)").font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
            }
        }
        .formStyle(.grouped)
        .task {
            await state.loadConfig()
            if let s = state.config?.server, !loaded { interval = s.sync_every_minutes ?? 30; loaded = true }
        }
    }
}
