# Web compatibility reconciliation

## Native focus coverage

`native_focus_transfer_reaches_real_widget_bridge` constructs the DOM through the real `WidgetBridge` and its JavaScript prelude. It registers native `focus` and bubbling `focusin` listeners, installs a `:focus-within` stylesheet, transfers focus with keyboard and pointer causes, and verifies blur and `focusout`. The same test observes `focusVisible` and the mapped cause string for both transfers.

`element_focus_and_blur_cover_native_and_fallback_paths` covers `Element.focus()` and `Element.blur()` when `setFocus` and `clearFocus` succeed, then covers their fallback event paths when a disabled native target makes those calls return false.

The bridge test is intentionally free of `__dispatch__` and `on()` stubs. Removing the `WidgetBridge` focus hooks, removing the `__pulpApplyNativeFocus__` call in `bridge_dispatch.cpp`, or removing the `FocusCause` string mapping makes the corresponding assertions fail. Each removal was checked as a separate temporary revert and restored before this lane was committed.

## Disabled focus targets

`disabled_focus_target_leaves_existing_focus_unchanged` verifies that `transfer_input_focus` returns true for an effectively disabled target without blurring the existing focused view. This matches Pulp's current `pointer_dispatch.cpp` behavior.

## Direct focus hooks

Pulp still has the same direct `on_focus_changed(true)` and `claim_input_focus()` calls at the corresponding text editor, virtual list, virtual grid, and label inline-editor sites. Its iOS host also directly claims a focusable hit target. These are retained in Vellum to preserve the current Pulp behavior at those non-bridge seams.

## Tab handler

The macOS Tab handler now performs the focus transfer once and refreshes `_focusedView` once. The previous success and failure branches assigned the same value.
