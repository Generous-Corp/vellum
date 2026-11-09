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

    public var backingScaleSource: (@MainActor () -> CGFloat)?
    public var drawableSize: CGSize { metalLayer.drawableSize }
    public var layerContentsScale: CGFloat { metalLayer.contentsScale }

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

    public struct SurfaceSize: Equatable, Sendable {
        public let width: UInt32
        public let height: UInt32
        public let scale: Float
        public let drawable: CGSize

        public init(width: UInt32, height: UInt32, scale: Float, drawable: CGSize) {
            self.width = width
            self.height = height
            self.scale = scale
            self.drawable = drawable
        }
    }

    public static let maximumPixelExtent: CGFloat = 16384

    public static func validatedSurfaceSize(_ size: CGSize, scale: CGFloat) -> SurfaceSize? {
        guard size.width.isFinite, size.height.isFinite, scale.isFinite,
              size.width >= 1, size.height >= 1, scale > 0 else { return nil }
        let width = size.width.rounded(.down)
        let height = size.height.rounded(.down)
        let pixelWidth = (width * scale).rounded(.up)
        let pixelHeight = (height * scale).rounded(.up)
        guard pixelWidth >= 1, pixelHeight >= 1,
              pixelWidth <= maximumPixelExtent, pixelHeight <= maximumPixelExtent,
              let pointWidth = UInt32(exactly: width),
              let pointHeight = UInt32(exactly: height) else { return nil }
        let narrowed = Float(scale)
        guard narrowed.isFinite, narrowed > 0 else { return nil }
        return SurfaceSize(width: pointWidth, height: pointHeight, scale: narrowed,
                           drawable: CGSize(width: pixelWidth, height: pixelHeight))
    }

    public override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        guard window != nil else {
            pumpTimer?.invalidate()
            pumpTimer = nil
            return
        }
        if surface == nil, let size = validatedSize(bounds.size) {
            metalLayer.contentsScale = CGFloat(size.scale)
            metalLayer.drawableSize = size.drawable
            perform {
                surface = try VellumSurface(width: size.width, height: size.height,
                                            scale: size.scale, metalLayer: metalLayer)
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
        resizeSurface(to: bounds.size)
    }

    public var backingScale: Float {
        Float(rawBackingScale)
    }

    private var rawBackingScale: CGFloat {
        backingScaleSource?() ?? window?.backingScaleFactor ?? metalLayer.contentsScale
    }

    private func validatedSize(_ size: CGSize) -> SurfaceSize? {
        let scale = rawBackingScale
        guard let validated = Self.validatedSurfaceSize(size, scale: scale) else {
            lastError = VellumHostError(
                status: VELLUM_HOST_ERROR_INVALID_ARGUMENT,
                message: "invalid surface size \(size.width)x\(size.height) at scale \(scale)")
            return nil
        }
        return validated
    }

    private func resizeSurface(to size: NSSize) {
        guard let surface, let validated = validatedSize(size) else { return }
        metalLayer.contentsScale = CGFloat(validated.scale)
        metalLayer.drawableSize = validated.drawable
        perform {
            try surface.resize(width: validated.width, height: validated.height,
                               scale: validated.scale)
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
