import AppKit
import SwiftUI

public struct VellumView: NSViewRepresentable {
    private let bundle: String
    private let onError: (@MainActor (VellumHostError) -> Void)?

    public init(bundle: String, onError: (@MainActor (VellumHostError) -> Void)? = nil) {
        self.bundle = bundle
        self.onError = onError
    }

    public func makeNSView(context: Context) -> NSView {
        do {
            return VellumHostView(application: try VellumApplication(bundle: bundle))
        } catch let error as VellumHostError {
            onError?(error)
            return NSView()
        } catch {
            return NSView()
        }
    }

    public func updateNSView(_ nsView: NSView, context: Context) {
        (nsView as? VellumHostView)?.redraw()
    }
}
