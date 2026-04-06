#pragma once

#include <string>
#include <vector>
#include <optional>

namespace pulp::platform {

// File filter (e.g., {"Audio Files", "wav;flac;mp3"})
struct FileFilter {
    std::string description;
    std::string extensions; // Semicolon-separated: "wav;flac;mp3"
};

// Native file open/save dialogs
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
};

} // namespace pulp::platform
