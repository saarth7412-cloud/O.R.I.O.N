import Foundation

/// Connection settings. Kept outside the main actor so they can be read from anywhere.
enum HUDConfig {
    static let defaultPort = 8765
    static let initialBackoff: TimeInterval = 1
    static let maxBackoff: TimeInterval = 30

    /// Where the HUD connects.
    /// - ORION_EVENTS_URL: full WebSocket URL, if set (highest priority).
    /// - ORION_PORT: port number, if set. Builds ws://127.0.0.1:<port>/events.
    /// - Otherwise: ws://127.0.0.1:8765/events.
    static var eventsURL: URL {
        let env = ProcessInfo.processInfo.environment
        if let full = env["ORION_EVENTS_URL"], let url = URL(string: full) {
            return url
        }
        let port = env["ORION_PORT"].flatMap { Int($0) } ?? defaultPort
        return URL(string: "ws://127.0.0.1:\(port)/events")!
    }
}

/// One message from the backend.
/// Standard messages carry `state`, `text`, and `source`.
/// Engine event messages carry `type` (for example "hello" or "command.received").
struct OrionEvent: Decodable {
    let state: String?
    let text: String?
    let source: String?
    let type: String?
    let service: String?
}

/// Keeps a WebSocket connection to the backend and pushes updates into the HUD.
/// Reconnects with exponential backoff whenever the connection drops or cannot be made.
@MainActor
final class EventClient {

    private let url: URL
    private let model: HUDStatusModel
    private var loopTask: Task<Void, Never>?

    init(url: URL = HUDConfig.eventsURL, model: HUDStatusModel) {
        self.url = url
        self.model = model
    }

    func start() {
        guard loopTask == nil else { return }
        loopTask = Task {
            await self.runLoop()
        }
    }

    func stop() {
        loopTask?.cancel()
        loopTask = nil
        model.isConnected = false
        model.status = "Offline"
        model.detail = "Backend link stopped"
    }

    /// Delay before retry attempt number `attempt` (1-based): 1s, 2s, 4s ... capped at 30s.
    static func backoffDelay(forAttempt attempt: Int) -> TimeInterval {
        let exponent = min(max(attempt - 1, 0), 10)
        return min(HUDConfig.initialBackoff * pow(2, Double(exponent)), HUDConfig.maxBackoff)
    }

    private func runLoop() async {
        var attempt = 0

        while !Task.isCancelled {
            let socket = URLSession.shared.webSocketTask(with: url)
            socket.resume()

            do {
                try await openConnection(socket)
                attempt = 0
                model.isConnected = true
                model.detail = "Connected to backend"
                try await receiveMessages(socket)
            } catch {
                if Task.isCancelled { break }
                NSLog("[O.R.I.O.N.] EventClient: \(error.localizedDescription)")
            }

            socket.cancel(with: .goingAway, reason: nil)
            model.isConnected = false

            if Task.isCancelled { break }

            attempt += 1
            let delay = Self.backoffDelay(forAttempt: attempt)
            model.status = "Offline"
            model.detail = "Reconnecting in \(Int(delay))s..."

            do {
                try await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
            } catch {
                break
            }
        }
    }

    /// Announces this client to the server. The async send throws on a dead connection
    /// instead of waiting forever, so the reconnect logic always runs.
    private func openConnection(_ socket: URLSessionWebSocketTask) async throws {
        try await socket.send(.string("{\"client\": \"orion-hud\"}"))
        NSLog("[O.R.I.O.N.] EventClient: connected to \(url.absoluteString)")
    }

    private func receiveMessages(_ socket: URLSessionWebSocketTask) async throws {
        while !Task.isCancelled {
            let message = try await socket.receive()
            switch message {
            case .string(let text):
                handle(text)
            case .data(let data):
                handle(String(decoding: data, as: UTF8.self))
            @unknown default:
                break
            }
        }
    }

    private func handle(_ raw: String) {
        guard let data = raw.data(using: .utf8) else { return }
        do {
            let event = try JSONDecoder().decode(OrionEvent.self, from: data)
            model.apply(event)
        } catch {
            NSLog("[O.R.I.O.N.] EventClient: ignored unreadable message: \(raw)")
        }
    }
}
