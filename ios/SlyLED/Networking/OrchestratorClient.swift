// OrchestratorClient.swift — minimal SlyLED REST client for the iOS shell.
// Three endpoints in v0.1.0: GET /status, GET /api/settings, POST /api/brightness, POST /api/show/stop.
// Server config persists in UserDefaults.

import Foundation

@MainActor
final class OrchestratorClient: ObservableObject {
    @Published var host: String = UserDefaults.standard.string(forKey: "host") ?? ""
    @Published var port: Int = UserDefaults.standard.integer(forKey: "port") == 0 ? 8080 : UserDefaults.standard.integer(forKey: "port")

    func saveServer(host: String, port: Int) {
        self.host = host
        self.port = port
        UserDefaults.standard.set(host, forKey: "host")
        UserDefaults.standard.set(port, forKey: "port")
    }

    var baseURL: URL? {
        guard !host.isEmpty else { return nil }
        return URL(string: "http://\(host):\(port)/")
    }

    // —— Endpoints ——

    func getStatus() async throws -> StatusResponse {
        try await get("status")
    }

    func getSettings() async throws -> SettingsResponse {
        try await get("api/settings")
    }

    func setBrightness(_ value: Int) async throws {
        try await postJSON("api/brightness", body: ["value": value])
    }

    func stopShow() async throws {
        try await postJSON("api/show/stop", body: [:])
    }

    // —— Internals ——

    private func get<T: Decodable>(_ path: String) async throws -> T {
        guard let base = baseURL else { throw ClientError.noServer }
        let url = base.appendingPathComponent(path)
        var req = URLRequest(url: url)
        req.timeoutInterval = 4
        let (data, response) = try await URLSession.shared.data(for: req)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw ClientError.badStatus
        }
        return try JSONDecoder().decode(T.self, from: data)
    }

    private func postJSON(_ path: String, body: [String: Any]) async throws {
        guard let base = baseURL else { throw ClientError.noServer }
        let url = base.appendingPathComponent(path)
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: body)
        req.timeoutInterval = 4
        let (_, response) = try await URLSession.shared.data(for: req)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw ClientError.badStatus
        }
    }
}

enum ClientError: Error { case noServer, badStatus }

struct StatusResponse: Decodable {
    let firmwareVersion: String?
    let uptime: Double?
}

struct SettingsResponse: Decodable {
    let globalBrightness: Int?
    let runnerRunning: Bool?
}
