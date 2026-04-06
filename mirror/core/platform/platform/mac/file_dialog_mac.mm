#include <pulp/platform/file_dialog.hpp>

#ifdef __APPLE__
#import <Cocoa/Cocoa.h>

namespace pulp::platform {

static NSArray<UTType*>* make_content_types(const std::vector<FileFilter>& filters) {
    NSMutableArray<UTType*>* types = [NSMutableArray array];
    for (auto& filter : filters) {
        // Parse semicolon-separated extensions
        std::string exts = filter.extensions;
        size_t pos = 0;
        while (pos < exts.size()) {
            auto sep = exts.find(';', pos);
            auto ext = exts.substr(pos, sep == std::string::npos ? std::string::npos : sep - pos);
            if (!ext.empty()) {
                NSString* ns_ext = [NSString stringWithUTF8String:ext.c_str()];
                UTType* type = [UTType typeWithFilenameExtension:ns_ext];
                if (type) [types addObject:type];
            }
            pos = (sep == std::string::npos) ? exts.size() : sep + 1;
        }
    }
    return types.count > 0 ? types : nil;
}

std::optional<std::string> FileDialog::open_file(
    const std::string& title,
    const std::vector<FileFilter>& filters,
    const std::string& default_path) {
    @autoreleasepool {
        NSOpenPanel* panel = [NSOpenPanel openPanel];
        [panel setTitle:[NSString stringWithUTF8String:title.c_str()]];
        [panel setCanChooseFiles:YES];
        [panel setCanChooseDirectories:NO];
        [panel setAllowsMultipleSelection:NO];

        auto types = make_content_types(filters);
        if (types) [panel setAllowedContentTypes:types];

        if (!default_path.empty())
            [panel setDirectoryURL:[NSURL fileURLWithPath:[NSString stringWithUTF8String:default_path.c_str()]]];

        if ([panel runModal] == NSModalResponseOK) {
            return std::string([[[panel URL] path] UTF8String]);
        }
        return std::nullopt;
    }
}

std::vector<std::string> FileDialog::open_files(
    const std::string& title,
    const std::vector<FileFilter>& filters,
    const std::string& default_path) {
    @autoreleasepool {
        NSOpenPanel* panel = [NSOpenPanel openPanel];
        [panel setTitle:[NSString stringWithUTF8String:title.c_str()]];
        [panel setCanChooseFiles:YES];
        [panel setCanChooseDirectories:NO];
        [panel setAllowsMultipleSelection:YES];

        auto types = make_content_types(filters);
        if (types) [panel setAllowedContentTypes:types];

        if (!default_path.empty())
            [panel setDirectoryURL:[NSURL fileURLWithPath:[NSString stringWithUTF8String:default_path.c_str()]]];

        std::vector<std::string> results;
        if ([panel runModal] == NSModalResponseOK) {
            for (NSURL* url in [panel URLs])
                results.push_back(std::string([[url path] UTF8String]));
        }
        return results;
    }
}

std::optional<std::string> FileDialog::save_file(
    const std::string& title,
    const std::vector<FileFilter>& filters,
    const std::string& default_path,
    const std::string& default_name) {
    @autoreleasepool {
        NSSavePanel* panel = [NSSavePanel savePanel];
        [panel setTitle:[NSString stringWithUTF8String:title.c_str()]];

        auto types = make_content_types(filters);
        if (types) [panel setAllowedContentTypes:types];

        if (!default_path.empty())
            [panel setDirectoryURL:[NSURL fileURLWithPath:[NSString stringWithUTF8String:default_path.c_str()]]];
        if (!default_name.empty())
            [panel setNameFieldStringValue:[NSString stringWithUTF8String:default_name.c_str()]];

        if ([panel runModal] == NSModalResponseOK) {
            return std::string([[[panel URL] path] UTF8String]);
        }
        return std::nullopt;
    }
}

std::optional<std::string> FileDialog::choose_folder(
    const std::string& title,
    const std::string& default_path) {
    @autoreleasepool {
        NSOpenPanel* panel = [NSOpenPanel openPanel];
        [panel setTitle:[NSString stringWithUTF8String:title.c_str()]];
        [panel setCanChooseFiles:NO];
        [panel setCanChooseDirectories:YES];

        if (!default_path.empty())
            [panel setDirectoryURL:[NSURL fileURLWithPath:[NSString stringWithUTF8String:default_path.c_str()]]];

        if ([panel runModal] == NSModalResponseOK) {
            return std::string([[[panel URL] path] UTF8String]);
        }
        return std::nullopt;
    }
}

} // namespace pulp::platform

#else

namespace pulp::platform {
std::optional<std::string> FileDialog::open_file(const std::string&, const std::vector<FileFilter>&, const std::string&) { return std::nullopt; }
std::vector<std::string> FileDialog::open_files(const std::string&, const std::vector<FileFilter>&, const std::string&) { return {}; }
std::optional<std::string> FileDialog::save_file(const std::string&, const std::vector<FileFilter>&, const std::string&, const std::string&) { return std::nullopt; }
std::optional<std::string> FileDialog::choose_folder(const std::string&, const std::string&) { return std::nullopt; }
}

#endif
