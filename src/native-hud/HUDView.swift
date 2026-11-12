import SwiftUI

/// Observable state rendered by the HUD. Updated on the main actor.
@MainActor
final class HUDStatusModel: ObservableObject {
    @Published var title: String = "O.R.I.O.N."
    @Published var status: String = "Offline"
    @Published var detail: String = "Connecting to backend..."
    @Published var source: String = ""
    @Published var isConnected: Bool = false

    /// Backend states that mean the microphone is active or speech is being transcribed.
    static let voiceStates: Set<String> = ["listening", "transcribing"]

    var isVoiceActive: Bool {
        Self.voiceStates.contains(status.lowercased())
    }

    /// Applies a state message received from the backend.
    func apply(_ event: OrionEvent) {
        status = event.state
        if let text = event.text, !text.isEmpty {
            detail = text
        }
        source = event.source ?? ""
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
