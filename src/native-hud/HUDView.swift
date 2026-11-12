import SwiftUI

/// Observable state rendered by the HUD. Updated on the main actor.
@MainActor
final class HUDStatusModel: ObservableObject {
    @Published var title: String = "O.R.I.O.N."
    @Published var status: String = "Offline"
    @Published var detail: String = "Connecting to backend..."
    @Published var source: String = ""
    @Published var isConnected: Bool = false

    /// Standard states, keyed by lowercase name, with the display name shown in the HUD.
    static let standardStates: [String: String] = [
        "online": "Online",
        "standby": "Standby",
        "idle": "Standby",
        "listening": "Listening",
        "transcribing": "Transcribing",
        "thinking": "Thinking",
        "offline": "Offline",
    ]

    /// Engine event types that map onto a HUD state.
    private static let engineEventStates: [String: String] = [
        "hello": "Online",
        "command.received": "Thinking",
        "command.routing": "Thinking",
        "screen.capturing": "Thinking",
        "screen.captured": "Thinking",
        "vlm.querying": "Thinking",
        "command.completed": "Online",
        "command.failed": "Online",
    ]

    /// Backend states that mean the microphone is active or speech is being transcribed.
    static let voiceStates: Set<String> = ["listening", "transcribing"]

    var isVoiceActive: Bool {
        Self.voiceStates.contains(status.lowercased())
    }

    /// Returns the display name for a standard state, or the raw value if it is not standard.
    static func displayName(for raw: String) -> String {
        standardStates[raw.lowercased()] ?? raw
    }

    /// Applies one backend message. Standard `state` messages update the label directly.
    /// Engine `type` messages are mapped to a state. Unknown types are logged and ignored.
    func apply(_ event: OrionEvent) {
        if let state = event.state, !state.isEmpty {
            status = Self.displayName(for: state)
            if let text = event.text, !text.isEmpty {
                detail = text
            }
            source = event.source ?? ""
            return
        }

        guard let type = event.type else {
            NSLog("[O.R.I.O.N.] HUD: message had neither state nor type, ignored")
            return
        }

        source = "engine"

        if let mapped = Self.engineEventStates[type] {
            status = mapped
        } else {
            NSLog("[O.R.I.O.N.] HUD: unrecognized event type '\(type)', ignored")
            return
        }

        switch type {
        case "hello":
            detail = "Connected to \(event.service ?? "engine")"
        case "command.failed":
            detail = "Last command failed"
        case "command.completed":
            detail = "Last command finished"
        default:
            detail = "Working on your command"
        }
    }
}

/// Animated bars shown while the backend is listening or transcribing.
struct VoiceIndicator: View {
    let color: Color
    let label: String

    var body: some View {
        HStack(spacing: 8) {
            TimelineView(.animation) { context in
                let time = context.date.timeIntervalSinceReferenceDate
                HStack(alignment: .center, spacing: 3) {
                    ForEach(0..<5, id: \.self) { index in
                        Capsule()
                            .fill(color)
                            .frame(width: 3, height: barHeight(index: index, time: time))
                    }
                }
                .frame(height: 18)
            }

            Text(label)
                .font(.system(size: 11, weight: .medium, design: .monospaced))
                .foregroundStyle(color)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(color.opacity(0.15), in: Capsule())
    }

    /// Each bar moves on its own phase so the bars look like a live waveform.
    private func barHeight(index: Int, time: TimeInterval) -> CGFloat {
        let phase = time * 6 + Double(index) * 0.8
        let wave = (sin(phase) + 1) / 2
        return CGFloat(5 + wave * 13)
    }
}

/// Root SwiftUI view hosted inside the FloatingHUDPanel.
struct HUDView: View {
    @ObservedObject var model: HUDStatusModel

    private var isTranscribing: Bool {
        model.status.lowercased() == "transcribing"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Circle()
                    .fill(model.isConnected ? Color.green : Color.gray)
                    .frame(width: 8, height: 8)
                Text(model.title)
                    .font(.system(size: 13, weight: .semibold, design: .monospaced))
                    .foregroundStyle(.secondary)
                Spacer()
                if !model.source.isEmpty {
                    Text(model.source)
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(.tertiary)
                }
            }

            Text(model.status)
                .font(.system(size: 28, weight: .semibold, design: .rounded))
                .foregroundStyle(.primary)

            if model.isVoiceActive {
                VoiceIndicator(
                    color: isTranscribing ? .purple : .blue,
                    label: isTranscribing ? "Transcribing" : "Listening"
                )
                .transition(.opacity.combined(with: .scale(scale: 0.9)))
            }

            Text(model.detail)
                .font(.system(size: 13))
                .foregroundStyle(.secondary)

            Spacer()
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .animation(.easeInOut(duration: 0.25), value: model.isVoiceActive)
    }
}
