import Foundation

/// Finds and supervises the Python engine that does the actual syncing.
@MainActor
final class Engine {
    enum Launch { case bundled(String), python(repo: URL) }

    let port: Int
    let token: String
    private(set) var process: Process?
    private(set) var logLines: [String] = []
    var demo = ProcessInfo.processInfo.environment["RADAR_DEMO"] == "1"
    private(set) var launch: Launch?
    var onLog: ((String) -> Void)?
    var onExit: ((Int32) -> Void)?

    init() {
        token = UUID().uuidString.replacingOccurrences(of: "-", with: "") + UUID().uuidString.replacingOccurrences(of: "-", with: "")
        port = Engine.freePort()
    }

    static func freePort() -> Int {
        let sock = socket(AF_INET, SOCK_STREAM, 0)
        defer { close(sock) }
        var addr = sockaddr_in()
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = 0
        addr.sin_addr.s_addr = inet_addr("127.0.0.1")
        var len = socklen_t(MemoryLayout<sockaddr_in>.size)
        let bound = withUnsafePointer(to: &addr) { $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(sock, $0, len) } }
        guard bound == 0 else { return 5153 }
        _ = withUnsafeMutablePointer(to: &addr) { $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { getsockname(sock, $0, &len) } }
        return Int(UInt16(bigEndian: addr.sin_port))
    }

    /// Bundled binary first; then RADAR_ENGINE; then the source checkout next to this package (development).
    static func locate() -> Launch? {
        if let url = Bundle.main.url(forResource: "radar-engine", withExtension: nil, subdirectory: "engine"),
           FileManager.default.isExecutableFile(atPath: url.path) {
            return .bundled(url.path)
        }
        if let env = ProcessInfo.processInfo.environment["RADAR_ENGINE"], FileManager.default.isExecutableFile(atPath: env) {
            return .bundled(env)
        }
        var dir = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        for _ in 0..<6 {
            if FileManager.default.fileExists(atPath: dir.appendingPathComponent("radar/__main__.py").path) { return .python(repo: dir) }
            dir.deleteLastPathComponent()
        }
        var exe = Bundle.main.bundleURL
        for _ in 0..<6 {
            if FileManager.default.fileExists(atPath: exe.appendingPathComponent("radar/__main__.py").path) { return .python(repo: exe) }
            exe.deleteLastPathComponent()
        }
        return nil
    }

    func start() throws {
        guard process == nil else { return }
        guard let launch = Engine.locate() else {
            throw APIError(message: "Radar's sync engine is missing from the app bundle. Rebuild with scripts/build_app.sh.")
        }
        self.launch = launch
        let p = Process()
        var args = [demo ? "demo" : "serve", "--app-mode", "--no-open", "--port", String(port)]
        switch launch {
        case .bundled(let path):
            p.executableURL = URL(fileURLWithPath: path)
        case .python(let repo):
            p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
            args = ["python3", "-m", "radar"] + args
            p.currentDirectoryURL = repo
        }
        p.arguments = args
        var env = ProcessInfo.processInfo.environment
        env["RADAR_API_TOKEN"] = token
        env["RADAR_PARENT_PID"] = String(getpid())
        env["PYTHONUNBUFFERED"] = "1"
        if demo { env["RADAR_HOME"] = NSTemporaryDirectory() + "radar-demo" }
        p.environment = env
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        pipe.fileHandleForReading.readabilityHandler = { [weak self] h in
            let data = h.availableData
            if data.isEmpty { h.readabilityHandler = nil; return }      // EOF: stop, or this handler spins a core
            guard let text = String(data: data, encoding: .utf8) else { return }
            Task { @MainActor in self?.append(text) }
        }
        p.terminationHandler = { [weak self] proc in
            pipe.fileHandleForReading.readabilityHandler = nil
            let status = proc.terminationStatus
            Task { @MainActor in
                self?.append("[engine exited with status \(status)]")
                self?.process = nil
                self?.onExit?(status)
            }
        }
        try p.run()
        process = p
        append("[engine started on port \(port) via \(launchDescription)]")
    }

    var launchDescription: String {
        switch launch {
        case .bundled(let path): return "bundled engine (\(path))"
        case .python(let repo): return "python3 -m radar in \(repo.path)"
        case nil: return "not started"
        }
    }

    private func append(_ text: String) {
        for line in text.split(separator: "\n", omittingEmptySubsequences: true) {
            logLines.append(String(line))
            onLog?(String(line))
        }
        if logLines.count > 400 { logLines.removeFirst(logLines.count - 400) }
    }

    func stop() {
        guard let p = process else { return }
        onExit = nil
        p.terminationHandler = nil
        p.terminate()
        process = nil
    }
}
