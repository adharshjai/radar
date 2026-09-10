import SwiftUI

struct ContentView: View {
    @Environment(AppState.self) private var state
    @State private var showAdd = false

    var body: some View {
        @Bindable var state = state
        NavigationSplitView {
            sidebar
        } detail: {
            detail
        }
        .searchable(text: $state.searchText, placement: .toolbar, prompt: "Search everything")
        .onSubmit(of: .search) { state.selection = .search }
        .onChange(of: state.searchText) { _, new in state.searchChanged(new) }
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                Button { showAdd = true } label: { Label("Add Task", systemImage: "plus") }.help("Add my own task")
                    .popover(isPresented: $showAdd, arrowEdge: .bottom) { AddTaskPopover(isPresented: $showAdd) }
                Button { state.sync() } label: {
                    if state.syncing { ProgressView().controlSize(.small) } else { Label("Sync", systemImage: "arrow.clockwise") }
                }
                .disabled(state.syncing || state.isDemo).help(state.isDemo ? "Demo data: nothing to sync" : "Sync now (⌘R)")
            }
            ToolbarItem(placement: .status) {
                Text(state.isDemo ? "demo data" : (state.dashboard.last_sync.map { "synced \(Fmt.relative($0))" } ?? "never synced"))
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .alert("Something went wrong", isPresented: Binding(get: { state.lastError != nil }, set: { if !$0 { state.lastError = nil } })) {
            Button("OK") { state.lastError = nil }
        } message: { Text(state.lastError ?? "") }
    }

    private var sidebar: some View {
        @Bindable var state = state
        return List(selection: $state.selection) {
            Section("Radar") {
                row(.today, "Today", "sun.max", badge: state.dashboard.stats.overdue + state.dashboard.stats.today)
                row(.calendar, "Calendar", "calendar")
                row(.inbox, "Inbox", "tray", badge: state.dashboard.stats.unread_mail)
                row(.sites, "Sites & tools", "globe", badge: state.dashboard.stats.checks)
                row(.search, "Everything", "magnifyingglass")
            }
            Section("Courses") {
                if state.dashboard.courses.isEmpty {
                    Text("No courses yet").foregroundStyle(.secondary).font(.callout)
                }
                ForEach(state.dashboard.courses) { c in
                    HStack(spacing: 8) {
                        Circle().fill(state.courseColors[c.id] ?? .gray).frame(width: 9, height: 9)
                        Text(c.shortName).lineLimit(1)
                        Spacer()
                        if let n = c.open_count, n > 0 { Text("\(n)").font(.caption).foregroundStyle(.secondary) }
                    }
                    .tag(Screen.course(c.id))
                    .help(c.displayName)
                }
            }
            Section("System") {
                row(.status, "Status", "waveform.path.ecg")
            }
        }
        .listStyle(.sidebar)
        .navigationSplitViewColumnWidth(min: 200, ideal: 230, max: 320)
    }

    private func row(_ screen: Screen, _ title: String, _ symbol: String, badge: Int = 0) -> some View {
        HStack {
            Label(title, systemImage: symbol)
            Spacer()
            if badge > 0 {
                Text("\(badge)").font(.caption.weight(.semibold)).padding(.horizontal, 6).padding(.vertical, 1)
                    .background(Capsule().fill(Color.accentColor.opacity(0.2)))
            }
        }
        .tag(screen)
    }

    @ViewBuilder private var detail: some View {
        if let err = state.engineError {
            VStack(spacing: 14) {
                EmptyHint(symbol: "exclamationmark.octagon", title: "Radar's engine stopped", text: err)
                Button("Try again") { state.retryEngine() }.buttonStyle(.borderedProminent)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if !state.engineReady {
            VStack(spacing: 12) { ProgressView(); Text("Starting Radar…").foregroundStyle(.secondary) }
        } else if !state.dashboard.configured && state.dashboard.courses.isEmpty && !state.isDemo {
            WelcomeView()
        } else {
            switch state.selection ?? .today {
            case .today: TodayView()
            case .calendar: CalendarView()
            case .inbox: InboxView()
            case .sites: SitesView()
            case .search: SearchView()
            case .status: StatusView()
            case .course(let id): CourseView(courseID: id)
            }
        }
    }
}

struct AddTaskPopover: View {
    @Environment(AppState.self) private var state
    @Binding var isPresented: Bool
    @State private var title = ""
    @State private var hasDue = true
    @State private var due = Calendar.current.date(bySettingHour: 23, minute: 59, second: 0, of: Date().addingTimeInterval(86400)) ?? Date()
    @State private var courseID = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("New task").font(.headline)
            TextField("What needs doing?", text: $title).textFieldStyle(.roundedBorder).frame(width: 300)
            Toggle("Due", isOn: $hasDue)
            if hasDue { DatePicker("", selection: $due).labelsHidden() }
            Picker("Course", selection: $courseID) {
                Text("None").tag("")
                ForEach(state.dashboard.courses) { Text($0.shortName).tag($0.id) }
            }
            HStack {
                Spacer()
                Button("Cancel") { isPresented = false }
                Button("Add") {
                    let t = title.trimmingCharacters(in: .whitespaces)
                    guard !t.isEmpty else { return }
                    Task { await state.addTask(title: t, due: hasDue ? due : nil, courseID: courseID.isEmpty ? nil : courseID) }
                    isPresented = false
                }.keyboardShortcut(.defaultAction).disabled(title.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .padding(14)
    }
}
