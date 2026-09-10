import SwiftUI

struct WelcomeView: View {
    @Environment(\.openSettings) private var openSettings
    var body: some View {
        VStack(spacing: 18) {
            Image(systemName: "dot.radiowaves.left.and.right").font(.system(size: 56)).foregroundStyle(Color.accentColor)
            Text("Welcome to Radar").font(.largeTitle.weight(.semibold))
            Text("One place for everything due: Canvas assignments, LabFlow and other course platforms, syllabus dates, and your school email.")
                .font(.title3).foregroundStyle(.secondary).multilineTextAlignment(.center).frame(maxWidth: 520)
            VStack(alignment: .leading, spacing: 10) {
                step(1, "Connect Canvas", "Reuses the login already in your browser, so it works even when your school hides access tokens.")
                step(2, "Connect Outlook (optional)", "A Microsoft sign-in code, read-only.")
                step(3, "Press Sync", "Radar pulls everything and reminds you about what's due.")
            }
            .padding(.top, 6)
            Button {
                openSettings()
            } label: { Label("Open Settings", systemImage: "gearshape").padding(.horizontal, 8) }
            .buttonStyle(.borderedProminent).controlSize(.large)
            Text("Everything stays on this Mac. Radar only ever reads.").font(.caption).foregroundStyle(.tertiary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(40)
    }

    private func step(_ n: Int, _ title: String, _ text: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Text("\(n)").font(.headline).frame(width: 26, height: 26).background(Circle().fill(Color.accentColor.opacity(0.15)))
            VStack(alignment: .leading, spacing: 2) { Text(title).font(.headline); Text(text).font(.callout).foregroundStyle(.secondary) }
        }
        .frame(width: 440, alignment: .leading)
    }
}
