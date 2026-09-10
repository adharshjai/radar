import AppKit
import SwiftUI
import UserNotifications

final class AppDelegate: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {
    var state: AppState?

    func applicationDidFinishLaunching(_ notification: Notification) {
        if Bundle.main.bundleURL.pathExtension == "app" { UNUserNotificationCenter.current().delegate = self }
        NSApp.setActivationPolicy(.regular)
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationWillTerminate(_ notification: Notification) { state?.shutdown() }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool { true }
    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification) async -> UNNotificationPresentationOptions {
        [.banner, .sound, .badge]
    }
    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse) async {
        let target = response.notification.request.content.userInfo["url"] as? String
        await MainActor.run {
            if let s = target, !s.isEmpty, let u = URL(string: s) { NSWorkspace.shared.open(u) } else { NSApp.activate(ignoringOtherApps: true) }
        }
    }
}

@main
struct RadarApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @State private var state = AppState()

    var body: some Scene {
        Window("Radar", id: "main") {
            ContentView()
                .environment(state)
                .frame(minWidth: 1080, minHeight: 600)
                .onAppear {
                    delegate.state = state
                    if !state.engineReady, state.engineError == nil { state.boot() }
                }
        }
        .defaultSize(width: 1240, height: 820)
        .commands {
            CommandGroup(after: .newItem) {
                Button("Sync Now") { state.sync() }.keyboardShortcut("r", modifiers: .command).disabled(state.syncing)
                Button("Open Web Dashboard") { if let u = state.webURL { NSWorkspace.shared.open(u) } }
            }
        }

        Settings {
            SettingsView().environment(state)
        }

        MenuBarExtra {
            MenuBarView().environment(state)
        } label: {
            let n = state.dashboard.stats.overdue + state.dashboard.stats.today
            Label(n > 0 ? "\(n)" : "", systemImage: state.dashboard.stats.overdue > 0 ? "dot.radiowaves.left.and.right" : "dot.radiowaves.right")
                .labelStyle(.titleAndIcon)
        }
        .menuBarExtraStyle(.window)
    }
}
