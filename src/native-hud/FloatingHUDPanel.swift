import AppKit

/// Non-activating, borderless, always-on-top HUD panel for O.R.I.O.N.
/// Does not steal focus from the frontmost application.
final class FloatingHUDPanel: NSPanel {

    static let defaultSize = NSSize(width: 560, height: 360)

    private let effectView = NSVisualEffectView()

    init() {
        super.init(
            contentRect: NSRect(origin: .zero, size: FloatingHUDPanel.defaultSize),
            styleMask: [.borderless, .nonactivatingPanel, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        configureWindow()
        configureContent()
    }

    required init?(coder: NSCoder) {
        fatalError("FloatingHUDPanel is constructed programmatically only.")
    }

    // MARK: - Focus behavior

    /// Allows keyboard input into the HUD without activating the app.
    override var canBecomeKey: Bool { true }

    /// The HUD is never the main window of the application.
    override var canBecomeMain: Bool { false }

    /// Escape dismisses the HUD.
    override func cancelOperation(_ sender: Any?) {
        hide()
    }

    // MARK: - Public API

    /// Embeds arbitrary content (SwiftUI-hosted or AppKit) inside the frosted HUD surface.
    func setHUDContent(_ view: NSView) {
        view.translatesAutoresizingMaskIntoConstraints = false
        effectView.addSubview(view)
        NSLayoutConstraint.activate([
            view.leadingAnchor.constraint(equalTo: effectView.leadingAnchor),
            view.trailingAnchor.constraint(equalTo: effectView.trailingAnchor),
            view.topAnchor.constraint(equalTo: effectView.topAnchor),
            view.bottomAnchor.constraint(equalTo: effectView.bottomAnchor)
        ])
    }

    func toggleVisibility() {
        if isVisible {
            hide()
        } else {
            present()
        }
    }

    /// Shows the HUD on the screen under the cursor without activating the app.
    func present() {
        centerOnCursorScreen()
        orderFrontRegardless()
    }

    func hide() {
        orderOut(nil)
    }

    // MARK: - Configuration

    private func configureWindow() {
        isReleasedWhenClosed = false
        isFloatingPanel = true
        level = .floating
        collectionBehavior = [
            .canJoinAllSpaces,
            .fullScreenAuxiliary,
            .stationary,
            .ignoresCycle
        ]

        isOpaque = false
        backgroundColor = .clear
        hasShadow = true

        titleVisibility = .hidden
        titlebarAppearsTransparent = true
        isMovableByWindowBackground = true

        hidesOnDeactivate = false
        becomesKeyOnlyIfNeeded = true
        animationBehavior = .utilityWindow
    }

    private func configureContent() {
        let container = NSView(frame: NSRect(origin: .zero, size: FloatingHUDPanel.defaultSize))
        container.wantsLayer = true
        container.layer?.backgroundColor = NSColor.clear.cgColor

        effectView.frame = container.bounds
        effectView.autoresizingMask = [.width, .height]
        effectView.material = .hudWindow
        effectView.blendingMode = .behindWindow
        effectView.state = .active
        effectView.wantsLayer = true
        effectView.layer?.cornerRadius = 18
        effectView.layer?.masksToBounds = true
        effectView.layer?.borderWidth = 0.5
        effectView.layer?.borderColor = NSColor.white.withAlphaComponent(0.15).cgColor

        container.addSubview(effectView)
        contentView = container
    }

    // MARK: - Positioning

    private func centerOnCursorScreen() {
        let mouse = NSEvent.mouseLocation
        let screen = NSScreen.screens.first { NSMouseInRect(mouse, $0.frame, false) } ?? NSScreen.main
        guard let visible = screen?.visibleFrame else { return }

        // Slightly above vertical center, which is the usual HUD resting position.
        let origin = NSPoint(
            x: visible.midX - frame.width / 2,
            y: visible.midY - frame.height / 2 + visible.height * 0.15
        )
        setFrameOrigin(origin)
    }
}
