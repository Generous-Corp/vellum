# Follow-up platform closure

This lane proves the macOS RmlUi PluginView bridge and its mirror link boundary. Windows and Linux remain gated because the mirror view unit is not yet packaged and consumed through verified platform closures on those targets.

The next PR should package `Vellum::MirrorView_core` and its public headers for Windows and Linux, add installed-consumer coverage for each platform, and run the platform-native RmlUi CTests with the mirror enabled. Keep the platform work separate from this folder-level provenance exemption.
