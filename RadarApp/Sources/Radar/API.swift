import Foundation

struct APIError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

/// Thin HTTP client for the local engine.
final class API: Sendable {
    let base: URL
    let token: String
    private let session: URLSession

    init(port: Int, token: String) {
        base = URL(string: "http://127.0.0.1:\(port)")!
        self.token = token
        let cfg = URLSessionConfiguration.ephemeral
        cfg.timeoutIntervalForRequest = 60
        session = URLSession(configuration: cfg)
    }

    private func request(_ path: String, method: String = "GET", body: Any? = nil, query: [String: String] = [:]) throws -> URLRequest {
        var comps = URLComponents(url: base.appendingPathComponent(path), resolvingAgainstBaseURL: false)!
        if !query.isEmpty { comps.queryItems = query.map { URLQueryItem(name: $0.key, value: $0.value) } }
        var req = URLRequest(url: comps.url!)
        req.httpMethod = method
        req.setValue(token, forHTTPHeaderField: "X-Radar-Token")
        if let body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        return req
    }

    private func send(_ req: URLRequest) async throws -> Data {
        let (data, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse else { throw APIError(message: "no response") }
        if http.statusCode >= 400 {
            if let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any], let err = obj["error"] as? String {
                throw APIError(message: err)
            }
            throw APIError(message: "Engine returned HTTP \(http.statusCode)")
        }
        return data
    }

    func get<T: Decodable>(_ path: String, query: [String: String] = [:], as type: T.Type) async throws -> T {
        let data = try await send(try request(path, query: query))
        return try JSONDecoder().decode(T.self, from: data)
    }

    @discardableResult
    func post(_ path: String, _ body: [String: Any] = [:]) async throws -> [String: Any] {
        let data = try await send(try request(path, method: "POST", body: body))
        return (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
    }

    @discardableResult
    func put(_ path: String, _ body: [String: Any]) async throws -> [String: Any] {
        let data = try await send(try request(path, method: "PUT", body: body))
        return (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
    }

    func delete(_ path: String) async throws {
        _ = try await send(try request(path, method: "DELETE"))
    }

    func health() async -> Bool {
        guard let req = try? request("/api/health") else { return false }
        if let (_, resp) = try? await session.data(for: req), let http = resp as? HTTPURLResponse { return http.statusCode == 200 }
        return false
    }
}
