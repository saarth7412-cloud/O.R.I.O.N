import SwiftUI

/// Observable state rendered by the HUD. Updated on the main actor.
@MainActor
final class HUDStatusModel: ObservableObject {
    @Published var title: String = "O.R.I.O.N."
    @Published var status: String = "Offline"
    @Published var detail: String = "Connecting to backend..."
    @Published var source: String = ""
    @Published var isConnected: Bool = false

    /// Applies a state message received from the backend.
    func apply(_ event: OrionEvent) {
        status = event.state
        if let text = event.text, !text.isEmpty {
            detail = text
        }
        source = event.source ?? ""
    }
}

/// Root SwiftUI view hosted inside the FloatingHUDPanel.
struct HUDView: View {
    @ObservedObject var model: HUDStatusModel

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

            Text(model.detail)
                .font(.system(size: 13))
                .foregroundStyle(.secondary)

            Spacer()
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}
