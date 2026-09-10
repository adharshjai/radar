import AppKit
import Observation
import ServiceManagement
import SwiftUI
import UserNotifications

enum Screen: Hashable {
    case today, calendar, inbox, sites, search, status
    case course(String)
}

@MainActor
@Observable
final class AppState {
    let engine = Engine()
    private(set) var api: API?
    var dashboard: Dashboard = .empty
    var selection: Screen? = .today
    var searchText = ""
    var engineReady = false
    var engineError: String?
    var lastError: String?
    var syncing = false
    var syncLog: [String] = []
    var config: ConfigSummary?
    var courseColors: [String: Color] = [:]
    var notificationsEnabled: Bool {
        get { UserDefaults.standard.object(forKey: "notifications") as? Bool ?? true }
        set { UserDefaults.standard.set(newValue, forKey: "notifications") }
    }
    private var pollTask: Task<Void, Never>?
    private var syncPollTask: Task<Void, Never>?

    var isDemo: Bool { engine.demo }
    var webURL: URL? { api.map { URL(string: "\($0.base.absoluteString)/?token=\($0.token)")! } }

    // MARK: - lifecycle

    func boot() {
        engine.onLog = { [weak self] line in self?.syncLog.append(line) }
        do {
            try engine.start()
        } catch {
            engineError = error.localizedDescription
            return
        }
        api = API(port: engine.port, token: engine.token)
        Task { await waitForEngine() }
    }

    private func waitForEngine() async {
        guard let api else { return }
        for _ in 0..<150 {
            if await api.health() {
                engineReady = true
                engineError = nil
                await refresh()
                await loadConfig()
                startPolling()
                requestNotificationPermission()
                return
            }
            try? await Task.sleep(for: .milliseconds(200))
        }
        engineError = "The sync engine did not start. " + (engine.logLines.suffix(3).joined(separator: " · "))
    }

    func shutdown() {
        pollTask?.cancel()
        syncPollTask?.cancel()
        engine.stop()
    }

    private func startPolling() {
        pollTask?.cancel()
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(25))
                await self?.refresh()
            }
        }
    }

    // MARK: - data

    func refresh() async {
        guard let api else { return }
        do {
            let d = try await api.get("/api/dashboard", as: Dashboard.self)
            dashboard = d
            courseColors = Palette.courseColors(d.courses)
            syncing = d.syncing
            updateBadge()
            if d.syncing, syncPollTask == nil { watchSync() }
        } catch {
            lastError = error.localizedDescription
        }
    }

    func loadConfig() async {
        guard let api else { return }
        if let c = try? await api.get("/api/config", as: ConfigSummary.self) { config = c }
    }

    func saveConfig(_ patch: [String: Any]) async -> Bool {
        guard let api else { return false }
        do {
            _ = try await api.put("/api/config", patch)
            await loadConfig()
            return true
        } catch {
            lastError = error.localizedDescription
            return false
        }
    }

    private func updateBadge() {
        let n = dashboard.stats.overdue + dashboard.stats.today
        NSApplication.shared.dockTile.badgeLabel = n > 0 ? String(n) : nil
    }

    // MARK: - sync

    func sync() {
        guard let api, !syncing else { return }
        syncing = true
        Task {
            do { _ = try await api.post("/api/sync") } catch { lastError = error.localizedDescription; syncing = false; return }
            watchSync()
        }
    }

    private func watchSync() {
        guard let api else { return }
        syncPollTask?.cancel()
        syncPollTask = Task { [weak self] in
            struct Status: Decodable { let syncing: Bool; let log: [String] }
            while !Task.isCancelled {
                try? await Task.sleep(for: .milliseconds(1500))
                guard let s = try? await api.get("/api/sync/status", as: Status.self) else { continue }
                await MainActor.run {
                    self?.syncLog = s.log
                    self?.syncing = s.syncing
                }
                if !s.syncing {
                    await self?.refresh()
                    await self?.loadConfig()
                    await self?.deliverNotifications()
                    await MainActor.run { self?.syncPollTask = nil }
                    return
                }
            }
        }
    }

    // MARK: - item actions

    private func act(_ uid: String, _ body: [String: Any]) {
        guard let api else { return }
        Task {
            do { _ = try await api.post("/api/item/\(uid)/state", body); await refresh() } catch { lastError = error.localizedDescription }
        }
    }

    func markDone(_ item: Item) {
        var body: [String: Any] = ["action": "done"]
        if let ids = item.extra?.site_ids { body["site_ids"] = ids }
        act(item.uid, body)
    }
    func reopen(_ item: Item) { act(item.uid, ["action": "reopen"]) }
    func snooze(_ item: Item, hours: Double) { act(item.uid, ["action": "snooze", "hours": hours]) }
    func snoozeUntilDue(_ item: Item) { act(item.uid, ["action": "snooze", "until": "due"]) }
    func setNote(_ item: Item, _ note: String) { act(item.uid, ["action": "note", "note": note]) }

    func addTask(title: String, due: Date?, courseID: String?) async {
        guard let api else { return }
        var body: [String: Any] = ["title": title]
        if let due {
            let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd'T'HH:mm"
            body["due"] = f.string(from: due)
        }
        if let courseID { body["course_id"] = courseID }
        do { _ = try await api.post("/api/item", body); await refresh() } catch { lastError = error.localizedDescription }
    }

    func deleteTask(_ item: Item) {
        guard let api else { return }
        Task { try? await api.delete("/api/item/\(item.uid)"); await refresh() }
    }

    func open(_ item: Item) {
        guard let api else { return }
        if item.kind == "site_check", let path = item.url {   // /sites/<id>/open records the visit, then redirects
            if let target = item.extra?.external_url, let u = URL(string: target) {
                Task { _ = try? await api.post("/sites/\(item.extra?.site_ids?.first ?? 0)/check"); await refresh() }
                NSWorkspace.shared.open(u)
            } else if let u = URL(string: "\(api.base.absoluteString)\(path)?token=\(api.token)") {
                NSWorkspace.shared.open(u)
            }
            return
        }
        if let u = item.openURL { NSWorkspace.shared.open(u) }
    }

    func visited() {
        guard let api else { return }
        Task { _ = try? await api.post("/visited") }
    }

    // MARK: - sites

    func openSite(_ site: Site) {
        guard let api else { return }
        Task { _ = try? await api.post("/sites/\(site.id)/check"); await refresh() }
        if let u = site.openURL { NSWorkspace.shared.open(u) }
    }
    func markSiteChecked(_ site: Site) { guard let api else { return }; Task { _ = try? await api.post("/sites/\(site.id)/check"); await refresh() } }
    func setSiteReminder(_ site: Site, days: Int) { guard let api else { return }; Task { _ = try? await api.post("/sites/\(site.id)/every", ["days": days]); await refresh() } }
    func hideSite(_ site: Site) { guard let api else { return }; Task { _ = try? await api.post("/sites/\(site.id)/hide"); await refresh() } }

    // MARK: - fetch helpers for detail screens

    func calendar(days: Int) async -> [CalendarDay] {
        guard let api else { return [] }
        struct R: Decodable { let days: [CalendarDay] }
        return (try? await api.get("/api/calendar", query: ["days": String(days)], as: R.self))?.days ?? []
    }
    func inbox(all: Bool) async -> [Item] {
        guard let api else { return [] }
        struct R: Decodable { let mail: [Item] }
        return (try? await api.get("/api/inbox", query: ["all": all ? "1" : "0"], as: R.self))?.mail ?? []
    }
    func sites() async -> [Site] {
        guard let api else { return [] }
        struct R: Decodable { let sites: [Site] }
        return (try? await api.get("/api/sites", as: R.self))?.sites ?? []
    }
    func course(_ id: String) async -> CourseDetail? {
        guard let api else { return nil }
        return try? await api.get("/api/course/\(id)", as: CourseDetail.self)
    }
    func search(_ q: String, kind: String) async -> ([Item], [String]) {
        guard let api else { return ([], []) }
        struct R: Decodable { let items: [Item]; let kinds: [String] }
        let r = try? await api.get("/api/search", query: ["q": q, "kind": kind], as: R.self)
        return (r?.items ?? [], r?.kinds ?? [])
    }
    func status() async -> StatusInfo? {
        guard let api else { return nil }
        return try? await api.get("/api/status", as: StatusInfo.self)
    }
    func brief() async -> String {
        guard let api else { return "" }
        struct R: Decodable { let text: String }
        return (try? await api.get("/api/brief", as: R.self))?.text ?? ""
    }

    // MARK: - accounts

    struct LoginResult { let ok: Bool; let message: String }

    func canvasLogin(baseURL: String, method: String, browser: String?, token: String?, cookie: String?) async -> LoginResult {
        guard let api else { return LoginResult(ok: false, message: "engine not ready") }
        var body: [String: Any] = ["base_url": baseURL, "method": method]
        if let browser, !browser.isEmpty { body["browser"] = browser }
        if let token { body["token"] = token }
        if let cookie { body["cookie"] = cookie }
        do {
            let r = try await api.post("/api/canvas/login", body)
            await loadConfig()
            let who = [r["name"] as? String, r["email"] as? String].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · ")
            return LoginResult(ok: true, message: "Connected as \(who)")
        } catch {
            return LoginResult(ok: false, message: error.localizedDescription)
        }
    }

    func canvasLogout() async {
        guard let api else { return }
        _ = try? await api.post("/api/canvas/logout")
        await loadConfig()
    }

    struct DeviceCode { let code: String; let url: String; let message: String }

    func outlookLoginStart() async -> Result<DeviceCode, APIError> {
        guard let api else { return .failure(APIError(message: "engine not ready")) }
        do {
            let r = try await api.post("/api/outlook/login/start")
            return .success(DeviceCode(code: r["user_code"] as? String ?? "", url: r["verification_uri"] as? String ?? "https://microsoft.com/devicelogin",
                                       message: r["message"] as? String ?? ""))
        } catch let e as APIError { return .failure(e) } catch { return .failure(APIError(message: error.localizedDescription)) }
    }

    func outlookLoginStatus() async -> (state: String, account: String?, error: String?) {
        guard let api else { return ("error", nil, "engine not ready") }
        struct R: Decodable { let state: String; let account: String?; let error: String? }
        let r = try? await api.get("/api/outlook/login/status", as: R.self)
        return (r?.state ?? "error", r?.account, r?.error)
    }

    func outlookLogout() async {
        guard let api else { return }
        _ = try? await api.post("/api/outlook/logout")
        await loadConfig()
    }

    // MARK: - notifications & login item

    private func requestNotificationPermission() {
        guard Bundle.main.bundleIdentifier != nil, Bundle.main.bundleURL.pathExtension == "app" else { return }
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .badge, .sound]) { _, _ in }
    }

    func deliverNotifications() async {
        guard let api, notificationsEnabled, Bundle.main.bundleURL.pathExtension == "app" else { return }
        struct R: Decodable { let items: [Item] }
        guard let r = try? await api.post("/api/notifications/claim"),
              let data = try? JSONSerialization.data(withJSONObject: r),
              let items = try? JSONDecoder().decode(R.self, from: data).items, !items.isEmpty else { return }
        let center = UNUserNotificationCenter.current()
        for item in items.prefix(5) {
            let content = UNMutableNotificationContent()
            content.title = item.title
            var parts: [String] = []
            if let c = item.course { parts.append(c) }
            if let d = item.due_text, !d.isEmpty { parts.append(d) } else { parts.append(Kind.label(item.kind)) }
            content.body = parts.joined(separator: " · ")
            content.sound = .default
            content.userInfo = ["uid": item.uid, "url": item.url ?? ""]
            try? await center.add(UNNotificationRequest(identifier: item.uid, content: content, trigger: nil))
        }
        if items.count > 5 {
            let content = UNMutableNotificationContent()
            content.title = "Radar"
            content.body = "\(items.count - 5) more new items are waiting in Radar."
            try? await center.add(UNNotificationRequest(identifier: "more-\(Date().timeIntervalSince1970)", content: content, trigger: nil))
        }
    }

    var launchAtLogin: Bool {
        get { SMAppService.mainApp.status == .enabled }
        set {
            do { if newValue { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() } }
            catch { lastError = "Could not change login item: \(error.localizedDescription)" }
        }
    }
}
