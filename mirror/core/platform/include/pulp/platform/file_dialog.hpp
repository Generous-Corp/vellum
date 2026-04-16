#pragma once

#include <string>
#include <vector>
#include <optional>
#include <functional>

namespace pulp::platform {

// File filter (e.g., {"Audio Files", "wav;flac;mp3"})
struct FileFilter {
    std::string description;
    std::string extensions; // Semicolon-separated: "wav;flac;mp3"
};

// Native file open/save dialogs.
//
// Platform notes (#301):
//   - macOS/iOS: native NSOpenPanel/NSSavePanel/UIDocumentPicker.
//   - Windows/Linux/Android: requires a host-registered Backend via
//     FileDialog::set_backend(). Without one, every call returns
//     std::nullopt / {} — explicitly "no backend" rather than
//     silent success. The Windows shell (IFileDialog), Linux
//     xdg-desktop-portal bridge, and Android SAF adapter live in
//     the host application layer and register a backend at
//     startup.
class FileDialog {
public:
    // Show an open file dialog. Returns selected path or nullopt.
    static std::optional<std::string> open_file(
        const std::string& title = "Open",
        const std::vector<FileFilter>& filters = {},
        const std::string& default_path = "");

    // Show an open file dialog allowing multiple selection.
    static std::vector<std::string> open_files(
        const std::string& title = "Open",
        const std::vector<FileFilter>& filters = {},
        const std::string& default_path = "");

    // Show a save file dialog. Returns selected path or nullopt.
    static std::optional<std::string> save_file(
        const std::string& title = "Save",
        const std::vector<FileFilter>& filters = {},
        const std::string& default_path = "",
        const std::string& default_name = "");

    // Show a folder selection dialog.
    static std::optional<std::string> choose_folder(
        const std::string& title = "Choose Folder",
        const std::string& default_path = "");

    // ── Host-registered backend (#301) ──────────────────────────────────
    //
    // On platforms without a native built-in backend (Windows,
    // Linux, Android) the host app can install a backend that
    // implements real native dialogs. Without a backend installed,
    // each call returns no-selection and the JS bridge can probe
    // `has_backend()` to distinguish "user cancelled" from
    // "platform unsupported".

    struct Backend {
        std::function<std::optional<std::string>(
            const std::string& title,
            const std::vector<FileFilter>& filters,
            const std::string& default_path)> open_file;
        std::function<std::vector<std::string>(
            const std::string& title,
            const std::vector<FileFilter>& filters,
            const std::string& default_path)> open_files;
        std::function<std::optional<std::string>(
            const std::string& title,
            const std::vector<FileFilter>& filters,
            const std::string& default_path,
            const std::string& default_name)> save_file;
        std::function<std::optional<std::string>(
            const std::string& title,
            const std::string& default_path)> choose_folder;
    };

    static void set_backend(Backend backend);
    static void clear_backend();
    static bool has_backend();
};

} // namespace pulp::platform
