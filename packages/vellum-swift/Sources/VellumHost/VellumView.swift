import AppKit
import CVellumHostC
import SwiftUI

@MainActor
public final class VellumViewContainer: NSView {
    public private(set) var hostView: VellumHostView?
    public private(set) var lastError: VellumHostError?
    public private(set) var bundle: String?

    public override var isFlipped: Bool { true }

    public func load(bundle: String) {
        guard bundle != self.bundle else { return }
        self.bundle = bundle
        hostView?.removeFromSuperview()
        hostView = nil
        do {
            let view = VellumHostView(application: try VellumApplication(bundle: bundle))
            view.frame = bounds
            view.autoresizingMask = [.width, .height]
            addSubview(view)
            hostView = view
            lastError = nil
        } catch let error as VellumHostError {
            lastError = error
        } catch {
            lastError = VellumHostError(status: VELLUM_HOST_ERROR_FAILED,
                                        message: String(describing: error))
        }
    }
}

public struct VellumView: NSViewRepresentable {
    private let bundle: String
    private let onError: (@MainActor (VellumHostError) -> Void)?

    public init(bundle: String, onError: (@MainActor (VellumHostError) -> Void)? = nil) {
        self.bundle = bundle
        self.onError = onError
    }

    public func makeNSView(context: Context) -> VellumViewContainer {
        let container = VellumViewContainer()
        load(into: container)
        return container
    }

    public func updateNSView(_ container: VellumViewContainer, context: Context) {
        load(into: container)
        container.hostView?.redraw()
    }

    private func load(into container: VellumViewContainer) {
        let previous = container.bundle
        container.load(bundle: bundle)
        if previous != bundle, let error = container.lastError { onError?(error) }
    }
}
