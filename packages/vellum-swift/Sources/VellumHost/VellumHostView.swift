import AppKit
import CVellumHostC
import Metal
import QuartzCore

@MainActor
public final class VellumHostView: NSView {
    public let application: VellumApplication
    public private(set) var surface: VellumSurface?
    public private(set) var lastError: VellumHostError?
    private let metalLayer = CAMetalLayer()
    private var pumpTimer: Timer?
    private var accessibilityElements: [String: NSAccessibilityElement] = [:]

    public init(application: VellumApplication) {
        self.application = application
        super.init(frame: NSRect(origin: .zero, size: application.viewport))
        wantsLayer = true
    }

    public required init?(coder: NSCoder) {
        nil
    }

    public override var isFlipped: Bool { true }
    public override var acceptsFirstResponder: Bool { true }
    public override var wantsUpdateLayer: Bool { true }

    public override func makeBackingLayer() -> CALayer {
        metalLayer.device = MTLCreateSystemDefaultDevice()
        metalLayer.pixelFormat = .bgra8Unorm
        metalLayer.framebufferOnly = false
        return metalLayer
    }

    public override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        guard let window else {
            pumpTimer?.invalidate()
            pumpTimer = nil
            return
        }
        if surface == nil {
            let scale = Float(window.backingScaleFactor)
            metalLayer.contentsScale = CGFloat(scale)
            metalLayer.drawableSize = CGSize(width: bounds.width * CGFloat(scale),
                                             height: bounds.height * CGFloat(scale))
            perform {
                surface = try VellumSurface(
                    width: UInt32(max(1, bounds.width)), height: UInt32(max(1, bounds.height)),
                    scale: scale, metalLayer: metalLayer)
                try surface?.render(application)
            }
        }
        if pumpTimer == nil {
            pumpTimer = Timer.scheduledTimer(withTimeInterval: 1.0 / 60.0, repeats: true) {
                [weak self] _ in
                MainActor.assumeIsolated { self?.pumpFrame() }
            }
        }
    }

    public override func setFrameSize(_ newSize: NSSize) {
        super.setFrameSize(newSize)
        resizeSurface(to: newSize)
    }

    public override func viewDidChangeBackingProperties() {
        super.viewDidChangeBackingProperties()
        metalLayer.contentsScale = window?.backingScaleFactor ?? metalLayer.contentsScale
        resizeSurface(to: bounds.size)
    }

    public var backingScale: Float {
        Float(window?.backingScaleFactor ?? metalLayer.contentsScale)
    }

    private func resizeSurface(to size: NSSize) {
        guard let surface, size.width >= 1, size.height >= 1 else { return }
        let scale = backingScale
        metalLayer.drawableSize = CGSize(width: size.width * CGFloat(scale),
                                         height: size.height * CGFloat(scale))
        perform {
            try surface.resize(width: UInt32(size.width), height: UInt32(size.height),
                               scale: scale)
            try surface.render(application)
        }
    }

    public override func mouseDown(with event: NSEvent) {
        let point = convert(event.locationInWindow, from: nil)
        perform {
            let outcome = try application.pointer(.down, at: point)
            if outcome == .focused { window?.makeFirstResponder(self) }
            if outcome != .ignored { redraw() }
        }
    }

    public override func mouseUp(with event: NSEvent) {
        let point = convert(event.locationInWindow, from: nil)
        perform { try application.pointer(.up, at: point) }
    }

    public override func keyDown(with event: NSEvent) {
        perform {
            let outcome: EventOutcome
            switch event.keyCode {
            case 36, 76:
                outcome = try application.key("Enter")
            case 51:
                outcome = try application.key("Backspace", isRepeat: event.isARepeat)
            default:
                let text = insertableText(event.characters)
                outcome = text.isEmpty ? .ignored : try application.insertText(text)
            }
            if outcome == .ignored {
                super.keyDown(with: event)
            } else {
                redraw()
            }
        }
    }

    public override func isAccessibilityElement() -> Bool { false }

    public override func accessibilityChildren() -> [Any]? {
        guard let nodes = try? application.accessibilityNodes() else { return [] }
        let live = Set(nodes.map(\.nodeID))
        accessibilityElements = accessibilityElements.filter { live.contains($0.key) }
        return nodes.map { node in
            let element = accessibilityElements[node.nodeID] ?? NSAccessibilityElement()
            accessibilityElements[node.nodeID] = element
            element.setAccessibilityParent(self)
            element.setAccessibilityRole(role(for: node.role))
            element.setAccessibilityLabel(node.label)
            element.setAccessibilityValue(node.value)
            element.setAccessibilityIdentifier(node.nodeID)
            element.setAccessibilityEnabled(!node.isDisabled)
            element.setAccessibilityFrameInParentSpace(node.bounds)
            return element
        }
    }

    public func redraw() {
        guard let surface else { return }
        perform { try surface.render(application) }
    }

    private func pumpFrame() {
        perform {
            if try application.pump().rendered { redraw() }
        }
    }

    private func insertableText(_ characters: String?) -> String {
        var scalars = String.UnicodeScalarView()
        for scalar in (characters ?? "").unicodeScalars
        where scalar.value >= 0x20 && scalar.value != 0x7F &&
            !(0xF700...0xF8FF).contains(scalar.value) {
            scalars.append(scalar)
        }
        return String(scalars)
    }

    private func role(for semanticRole: String) -> NSAccessibility.Role {
        switch semanticRole {
        case "button": .button
        case "text-field": .textField
        case "checkbox": .checkBox
        case "image": .image
        default: .staticText
        }
    }

    private func perform(_ body: () throws -> Void) {
        do {
            try body()
            lastError = nil
        } catch let error as VellumHostError {
            lastError = error
        } catch {
            lastError = VellumHostError(status: VELLUM_HOST_ERROR_FAILED,
                                        message: String(describing: error))
        }
    }
}
