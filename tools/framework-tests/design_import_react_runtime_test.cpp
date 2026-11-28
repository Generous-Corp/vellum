// React-runtime parser cluster, TSX/runtime React bundle parsers for
// every supported design-tool source. All four parsers share a
// consistent contract: parse fixture, materialize via host React shim,
// accept sanitized TSX, reject out-of-matrix surfaces.
//
// Covered:
//   * parse_v0_tsx + parse_v0_dev_react           (v0.dev)
//   * parse_figma_make_react                      (Figma Make)
//   * parse_stitch_react                          (Stitch)
//   * parse_react_native_export                   (React Native)
//   * parse_pencil_react                          (Pencil)

#include "framework_test.hpp"
#include <vellum/state/store.hpp>
#include <vellum/view/design_import.hpp>
#include <vellum/view/script_engine.hpp>
#include <vellum/view/widget_bridge.hpp>

#include <cstdlib>
#include <stdexcept>
#include <fstream>
#include <sstream>
#include <string>
#include <string_view>
#include <vector>

using namespace vellum::view;

// A failed prerequisite aborts the case before the test uses its result.
#define require(expression) \
    do { \
        if (!(expression)) throw std::runtime_error("Required: " #expression); \
    } while (false)

namespace {

#ifndef VELLUM_REACT_FIXTURE_DIR
#define VELLUM_REACT_FIXTURE_DIR "."
#endif

std::string read_fixture(const std::string& name) {
    const std::string path = std::string(VELLUM_REACT_FIXTURE_DIR) + "/" + name;
    std::ifstream in(path, std::ios::binary);
    if (!in.good())
        throw std::runtime_error("reading fixture failed: " + path);
    std::ostringstream ss;
    ss << in.rdbuf();
    return ss.str();
}

std::string asset_text(const ClaudeBundleAsset& asset) {
    return std::string(asset.data.begin(), asset.data.end());
}

const char* minimal_host_react_dom_shim() {
    return R"JS(
(function(){
  function flatten(input, out) {
    if (input == null || input === false || input === true) return;
    if (Array.isArray(input)) {
      for (var i = 0; i < input.length; i++) flatten(input[i], out);
      return;
    }
    out.push(input);
  }

  function createElement(type, props) {
    var children = [];
    for (var i = 2; i < arguments.length; i++) flatten(arguments[i], children);
    return { type: type, props: props || {}, children: children };
  }

  function cssValue(key, value) {
    if (value == null) return "";
    if (typeof value === "number") {
      if (key === "flexGrow" || key === "flexShrink" || key === "opacity" ||
          key === "zIndex" || key === "lineHeight") {
        return String(value);
      }
      return String(value) + "px";
    }
    return String(value);
  }

  function applyProps(el, props) {
    props = props || {};
    for (var key in props) {
      if (key === "children" || key === "key") continue;
      var value = props[key];
      if (key === "style" && value) {
        for (var styleKey in value) el.style[styleKey] = cssValue(styleKey, value[styleKey]);
      } else if (key === "ref" && value) {
        value.current = el;
      } else if (key === "className") {
        el.setAttribute("class", String(value));
      } else if (key.slice(0, 2) === "on") {
        el["__" + key] = value;
      } else if (value === true) {
        el.setAttribute(key, "");
      } else if (value !== false && value != null) {
        el.setAttribute(key, String(value));
      }
    }
  }

  function renderNode(node) {
    if (node == null || node === false || node === true) return null;
    if (typeof node === "string" || typeof node === "number") {
      return document.createTextNode(String(node));
    }
    if (typeof node.type === "function") {
      var props = Object.assign({}, node.props || {});
      props.children = node.children;
      return renderNode(node.type(props));
    }
    var el = document.createElement(node.type === globalThis.React.Fragment ? "span" : node.type);
    applyProps(el, node.props);
    for (var i = 0; i < node.children.length; i++) {
      var child = renderNode(node.children[i]);
      if (child) el.appendChild(child);
    }
    return el;
  }

  var effects = [];
  globalThis.React = {
    Fragment: "__fragment",
    createElement: createElement,
    useCallback: function(fn) { return fn; },
    useEffect: function(fn) { effects.push(fn); },
    useMemo: function(fn) { return fn(); },
    useRef: function(value) { return { current: value == null ? null : value }; },
    useState: function(initial) {
      var value = initial;
      return [value, function(next) { value = (typeof next === "function") ? next(value) : next; }];
    }
  };
  globalThis.ReactDOM = {
    createRoot: function(mount) {
      return {
        render: function(element) {
          var node = renderNode(element);
          if (node) mount.appendChild(node);
          for (var i = 0; i < effects.length; i++) effects[i]();
        }
      };
    },
    flushSync: function(fn) { return fn(); }
  };
})();
)JS";
}

void maybe_write_figma_runtime_script(const std::string& runtime_js) {
    const char* out = std::getenv("VELLUM_FIGMA_RUNTIME_JS_OUT");
    if (out == nullptr || *out == '\0') return;

    std::ofstream file(out, std::ios::binary);
    require(file.good());
    file << minimal_host_react_dom_shim() << "\n" << runtime_js << "\n";
}

void maybe_write_stitch_runtime_script(const std::string& runtime_js) {
    const char* out = std::getenv("VELLUM_STITCH_RUNTIME_JS_OUT");
    if (out == nullptr || *out == '\0') return;

    std::ofstream file(out, std::ios::binary);
    require(file.good());
    file << minimal_host_react_dom_shim() << "\n" << runtime_js << "\n";
}

void maybe_write_rn_runtime_script(const std::string& runtime_js) {
    const char* out = std::getenv("VELLUM_RN_RUNTIME_JS_OUT");
    if (out == nullptr || *out == '\0') return;

    std::ofstream file(out, std::ios::binary);
    require(file.good());
    file << minimal_host_react_dom_shim() << "\n" << runtime_js << "\n";
}

void maybe_write_pencil_runtime_script(const std::string& runtime_js) {
    const char* out = std::getenv("VELLUM_PENCIL_RUNTIME_JS_OUT");
    if (out == nullptr || *out == '\0') return;

    std::ofstream file(out, std::ios::binary);
    require(file.good());
    file << minimal_host_react_dom_shim() << "\n" << runtime_js << "\n";
}

} // namespace

// ── v0 TSX parsing ──────────────────────────────────────────────────────

FRAMEWORK_TEST(parse_v0_tsx_extracts_host_jsx_structure_and_classname_metadata) {
    auto tsx = R"(
        export default function Component() {
            return (
                <div className="flex flex-col gap-4 p-4 bg-slate-900">
                    <div className="flex flex-row gap-2">
                        <span>Label</span>
                    </div>
                </div>
            );
        }
    )";
    auto ir = parse_v0_tsx(tsx);

    require(ir.source == DesignSource::v0);
    require(ir.root.type == "frame");
    require(ir.root.attributes.count("className") == 1);
    require(ir.root.layout.direction == LayoutDirection::column);
    require(!ir.root.children.empty());
    require(ir.root.children[0].attributes.count("className") == 1);
}

FRAMEWORK_TEST(parse_v0_tsx_accepts_json_ir_directly) {
    auto json = R"({"type": "frame", "name": "V0Screen", "children": []})";
    auto ir = parse_v0_tsx(json);
    require(ir.root.name == "V0Screen");
}

FRAMEWORK_TEST(parse_v0_dev_react_parses_staged_v0_runtime_fixtures) {
    const auto primary = read_fixture("v0-dev_audio-control-panel.tsx");
    auto bundle = parse_v0_dev_react(primary);
    require(bundle.has_value());
    require(bundle->assets.size() == 1);
    require(bundle->javascript_indices.size() == 1);
    require(bundle->javascript_indices.front() == 0);
    require(bundle->assets.front().uuid == "v0-runtime-app");
    require(bundle->assets.front().mime == "text/javascript");
    require(bundle->template_html.find("data-pulp-source=\"v0\"") != std::string::npos);
    require(bundle->template_html.find("v0-audio-control-panel") != std::string::npos);

    const auto js = asset_text(bundle->assets.front());
    require(js.find("v0-audio-control-panel") != std::string::npos);
    require(js.find("ReactDOM.createRoot") != std::string::npos);
    require(js.find("requestAnimationFrame") != std::string::npos);
    require(js.find("performance.now") != std::string::npos);
    require(js.find("canvas.getContext('2d')") != std::string::npos);
    require(js.find("type: 'range'") != std::string::npos);

    const auto settings = read_fixture("v0-dev_settings-strip.tsx");
    auto settings_bundle = parse_v0_dev_react(settings);
    require(settings_bundle.has_value());
    require(asset_text(settings_bundle->assets.front()).find("v0-settings-strip") != std::string::npos);

    const auto transport = read_fixture("v0-dev_transport-meter.tsx");
    auto transport_bundle = parse_v0_dev_react(transport);
    require(transport_bundle.has_value());
    require(asset_text(transport_bundle->assets.front()).find("v0-transport-meter") != std::string::npos);

    const std::string multi_line_import = R"(
        import {
          useState
        } from "react";
        export default function MultiLineReactPanel() {
          const [level, setLevel] = useState(0.5);
          return (
            <div id="v0-multi-line-react-import">
              <span>Level</span>
              <input type="range" value={level} onChange={(event) => setLevel(Number(event.currentTarget.value))} />
            </div>
          );
        }
    )";
    auto multi_line_bundle = parse_v0_dev_react(multi_line_import);
    require(multi_line_bundle.has_value());
    require(asset_text(multi_line_bundle->assets.front()).find("v0-multi-line-react-import") != std::string::npos);
}

FRAMEWORK_TEST(parse_v0_dev_react_accepts_a_v0_file_envelope) {
    const auto primary = read_fixture("v0-dev_audio-control-panel.tsx");
    const auto envelope =
        std::string("[V0_FILE]json:file=\"package.json\"\n{\"dependencies\":{\"react\":\"latest\"}}\n")
        + "[V0_FILE]tsx:file=\"app/page.tsx\"\n"
        + primary
        + "\n[V0_FILE]tsx:file=\"app/ignored.tsx\"\n"
          "import { useState } from \"react\";\n"
          "export default function Ignored(){ return <div id=\"ignored\">Ignored</div>; }\n";

    auto bundle = parse_v0_dev_react(envelope);
    require(bundle.has_value());
    require(bundle->template_html.find("v0-audio-control-panel") != std::string::npos);
    require(asset_text(bundle->assets.front()).find("app/page.tsx") != std::string::npos);
}

FRAMEWORK_TEST(parse_v0_dev_react_rejects_out_of_matrix_v0_default_surfaces) {
    const std::vector<std::string> rejected = {
        R"(
            import { useState } from "react";
            export default function TailwindPanel() {
              return <div className="flex rounded-lg">Tailwind</div>;
            }
        )",
        R"(
            import { useState } from "react";
            import { Button } from "@/components/ui/button";
            export default function ShadcnPanel() {
              return <Button>Open</Button>;
            }
        )",
        R"(
            import {
              format
            } from "date-fns";
            export default function UnsupportedImportPanel() {
              return <div id="unsupported-import">Unsupported import</div>;
            }
        )",
        R"(
            import { useEffect } from "react";
            export default function DynamicImportPanel() {
              useEffect(() => { import("./meter"); }, []);
              return <div id="dynamic-import">Dynamic import</div>;
            }
        )",
        R"(
            import { useEffect } from "react";
            export default function NetworkPanel() {
              useEffect(() => { fetch("/api/state"); }, []);
              return <div id="network">Network</div>;
            }
        )",
        R"(
            import { useState } from "react";
            export default function TextInputPanel() {
              const [value, setValue] = useState("");
              return <input type="text" value={value} onChange={(event) => setValue(event.currentTarget.value)} />;
            }
        )",
        R"(
            import { useState } from "react";
            export default function CustomComponentPanel() {
              return <Slider value={0.5} />;
            }
        )"
    };

    for (const auto& sample : rejected) {
        require(!parse_v0_dev_react(sample).has_value());
    }
}

// ── Figma Make React parsing ────────────────────────────────────────────

FRAMEWORK_TEST(parse_figma_make_react_parses_staged_figma_runtime_fixture) {
    const auto primary = read_fixture("figma_level-meter-panel.tsx");
    auto bundle = parse_figma_make_react(primary);
    require(bundle.has_value());
    require(bundle->assets.size() == 1);
    require(bundle->javascript_indices.size() == 1);
    require(bundle->javascript_indices.front() == 0);
    require(bundle->assets.front().uuid == "figma-runtime-app");
    require(bundle->assets.front().mime == "text/javascript");
    require(bundle->template_html.find("data-pulp-source=\"figma\"") != std::string::npos);
    require(bundle->template_html.find("figma-level-meter-panel") != std::string::npos);

    const auto js = asset_text(bundle->assets.front());
    require(js.find("figma-level-meter-panel") != std::string::npos);
    require(js.find("Figma Make runtime import requires host React and ReactDOM") != std::string::npos);
    require(js.find("'data-pulp-source': 'figma'") != std::string::npos);
    require(js.find("ReactDOM.createRoot") != std::string::npos);
    require(js.find("requestAnimationFrame") != std::string::npos);
    require(js.find("performance.now") != std::string::npos);
    require(js.find("canvas.getContext('2d')") != std::string::npos);
    require(js.find("type: 'range'") != std::string::npos);
}

FRAMEWORK_TEST(parse_figma_make_react_runtime_bundle_materializes_with_host_react_shim) {
    const auto primary = read_fixture("figma_level-meter-panel.tsx");
    auto bundle = parse_figma_make_react(primary);
    require(bundle.has_value());
    const auto runtime_js = asset_text(bundle->assets.front());
    maybe_write_figma_runtime_script(runtime_js);

    vellum::state::StateStore store;
    ScriptEngine engine;
    View root;
    WidgetBridge bridge(engine, root, store);

    bridge.load_script(minimal_host_react_dom_shim());
    bridge.load_script(runtime_js);
    bridge.service_frame_callbacks();

    require(root.child_count() > 0);
    require(engine.evaluate("!!document.getElementById('figma-level-meter-panel')")
                .getWithDefault<bool>(false));
    require(!engine.evaluate(
        "(function(){ var el = document.getElementById('figma-level-meter-panel');"
        "return el ? String(el._id || '') : ''; })()").toString().empty());
}

FRAMEWORK_TEST(parse_figma_make_react_accepts_sanitized_figma_make_tsx) {
    const std::string sanitized = R"(
        // Source: Figma Make export (sanitized for runtime import)
        import {
          useState
        } from "react";
        export default function MultiLineFigmaPanel() {
          const [level, setLevel] = useState(0.5);
          return (
            <div id="figma-multi-line-react-import" style={{ display: "flex", flexDirection: "column" }}>
              <span>Level</span>
              <input type="range" value={level} onChange={(event) => setLevel(Number(event.currentTarget.value))} />
            </div>
          );
        }
    )";

    auto bundle = parse_figma_make_react(sanitized);
    require(bundle.has_value());
    require(bundle->template_html.find("figma-multi-line-react-import") != std::string::npos);
    require(asset_text(bundle->assets.front()).find("figma-multi-line-react-import") != std::string::npos);
}

FRAMEWORK_TEST(parse_figma_make_react_rejects_out_of_matrix_figma_defaults) {
    const std::vector<std::string> rejected = {
        R"(
            "use client";
            // Source: Figma Make export
            import { useState } from "react";
            export default function NextFigmaPanel() {
              return <div id="figma-next-panel">Next path</div>;
            }
        )",
        R"(
            // Source: Figma Make export
            import { useState } from "react";
            import hero from "figma:asset/a1b2c3d4.png";
            export default function FigmaAssetPanel() {
              return <div id="figma-asset-panel">Asset {hero}</div>;
            }
        )",
        R"(
            // Source: Figma Make export
            import { Dialog } from "@radix-ui/react-dialog@1.1.6";
            export default function VersionedImportPanel() {
              return <div id="figma-versioned-panel">Versioned</div>;
            }
        )",
        R"(
            import figma from "@figma/code-connect/react";
            figma.connect(Button, "https://figma.com/file/example", {
              example: () => <Button />
            });
        )",
        R"(
            // Source: Figma Make export
            import { useState } from "react";
            export default function TailwindPanel() {
              return <div className="flex rounded-lg">Tailwind</div>;
            }
        )",
        R"(
            // Source: Figma Make export
            import { useState } from "react";
            import Link from "next/link";
            export default function NextImportPanel() {
              return <Link href="/">Next</Link>;
            }
        )",
        R"(
            import { useState } from "react";
            export default function GenericReactPanel() {
              const [level, setLevel] = useState(0.5);
              return <input type="range" value={level} onChange={(event) => setLevel(Number(event.currentTarget.value))} />;
            }
        )",
        R"(
            // Source: Figma Make export
            import { useState } from "react";
            export default function TextInputPanel() {
              const [value, setValue] = useState("");
              return <input type="text" value={value} onChange={(event) => setValue(event.currentTarget.value)} />;
            }
        )"
    };

    for (const auto& sample : rejected) {
        require(!parse_figma_make_react(sample).has_value());
    }
}

// ── Google Stitch React parsing ────────────────────────────────────────

FRAMEWORK_TEST(parse_stitch_react_parses_staged_stitch_runtime_fixture) {
    const auto primary = read_fixture("stitch_transport-bar.tsx");
    auto bundle = parse_stitch_react(primary);
    require(bundle.has_value());
    require(bundle->assets.size() == 1);
    require(bundle->javascript_indices.size() == 1);
    require(bundle->javascript_indices.front() == 0);
    require(bundle->assets.front().uuid == "stitch-runtime-app");
    require(bundle->assets.front().mime == "text/javascript");
    require(bundle->template_html.find("data-pulp-source=\"stitch\"") != std::string::npos);
    require(bundle->template_html.find("stitch-transport-bar") != std::string::npos);

    const auto js = asset_text(bundle->assets.front());
    require(js.find("stitch-transport-bar") != std::string::npos);
    require(js.find("Stitch runtime import requires host React and ReactDOM") != std::string::npos);
    require(js.find("'data-pulp-source': 'stitch'") != std::string::npos);
    require(js.find("ReactDOM.createRoot") != std::string::npos);
    require(js.find("requestAnimationFrame") != std::string::npos);
    require(js.find("performance.now") != std::string::npos);
    require(js.find("canvas.getContext('2d')") != std::string::npos);
    require(js.find("type: 'range'") != std::string::npos);
}

FRAMEWORK_TEST(parse_stitch_react_runtime_bundle_materializes_with_host_react_shim) {
    const auto primary = read_fixture("stitch_transport-bar.tsx");
    auto bundle = parse_stitch_react(primary);
    require(bundle.has_value());
    const auto runtime_js = asset_text(bundle->assets.front());
    maybe_write_stitch_runtime_script(runtime_js);

    vellum::state::StateStore store;
    ScriptEngine engine;
    View root;
    WidgetBridge bridge(engine, root, store);

    bridge.load_script(minimal_host_react_dom_shim());
    bridge.load_script(runtime_js);
    bridge.service_frame_callbacks();

    require(root.child_count() > 0);
    require(engine.evaluate("!!document.getElementById('stitch-transport-bar')")
                .getWithDefault<bool>(false));
    require(!engine.evaluate(
        "(function(){ var el = document.getElementById('stitch-transport-bar');"
        "return el ? String(el._id || '') : ''; })()").toString().empty());
}

FRAMEWORK_TEST(parse_stitch_react_accepts_sanitized_stitch_tsx) {
    const std::string sanitized = R"(
        import {
          useState
        } from "react";
        export default function MultiLineStitchPanel() {
          const [level, setLevel] = useState(0.5);
          return (
            <div id="stitch-multi-line-react-import" data-stitch-screen="level" style={{ display: "flex", flexDirection: "column" }}>
              <span>Level</span>
              <input type="range" value={level} onChange={(event) => setLevel(Number(event.currentTarget.value))} />
            </div>
          );
        }
    )";

    auto bundle = parse_stitch_react(sanitized);
    require(bundle.has_value());
    require(bundle->template_html.find("stitch-multi-line-react-import") != std::string::npos);
    require(asset_text(bundle->assets.front()).find("stitch-multi-line-react-import") != std::string::npos);
}

FRAMEWORK_TEST(parse_stitch_react_rejects_out_of_matrix_stitch_defaults) {
    const std::vector<std::string> rejected = {
        R"(
            "use client";
            import { useState } from "react";
            export default function NextStitchPanel() {
              return <div id="stitch-next-panel">Next path</div>;
            }
        )",
        R"(
            import { useState } from "react";
            export default function TailwindPanel() {
              return <div className="flex rounded-lg">Tailwind</div>;
            }
        )",
        R"(
            import "./transport.css";
            import { useState } from "react";
            export default function ExternalCssPanel() {
              return <div id="stitch-css-panel">CSS</div>;
            }
        )",
        R"(
            import { Dialog } from "@radix-ui/react-dialog";
            export default function RadixPanel() {
              return <div id="stitch-radix-panel">Radix</div>;
            }
        )",
        R"(
            import Link from "next/link";
            export default function NextImportPanel() {
              return <Link href="/">Next</Link>;
            }
        )",
        R"(
            import { View } from "react-native";
            export default function NativePanel() {
              return <View />;
            }
        )",
        R"({"screen_id":"transport-bar","nodes":[]})",
        R"(
            import { useState } from "react";
            export default function TextInputPanel() {
              const [value, setValue] = useState("");
              return <input type="text" value={value} onChange={(event) => setValue(event.currentTarget.value)} />;
            }
        )",
        R"(
            import { useState } from "react";
            export default function CustomComponentPanel() {
              return <TransportSlider value={0.5} />;
            }
        )"
    };

    for (const auto& sample : rejected) {
        require(!parse_stitch_react(sample).has_value());
    }
}

// ── React Native export parsing ────────────────────────────────────────

FRAMEWORK_TEST(parse_react_native_export_parses_staged_rn_runtime_fixture) {
    const auto primary = read_fixture("rn_gain-stage.tsx");
    auto bundle = parse_react_native_export(primary);
    require(bundle.has_value());
    require(bundle->assets.size() == 1);
    require(bundle->javascript_indices.size() == 1);
    require(bundle->javascript_indices.front() == 0);
    require(bundle->assets.front().uuid == "rn-runtime-app");
    require(bundle->assets.front().mime == "text/javascript");
    require(bundle->template_html.find("data-pulp-source=\"rn\"") != std::string::npos);
    require(bundle->template_html.find("rn-gain-stage") != std::string::npos);

    const auto js = asset_text(bundle->assets.front());
    require(js.find("rn-gain-stage") != std::string::npos);
    require(js.find("React Native runtime import requires host React and ReactDOM") != std::string::npos);
    require(js.find("'data-pulp-source': 'rn'") != std::string::npos);
    require(js.find("'data-rn-default-flex': 'column'") != std::string::npos);
    require(js.find("flexDirection: 'column'") != std::string::npos);
    require(js.find("ReactDOM.createRoot") != std::string::npos);
    require(js.find("canvas.getContext") == std::string::npos);
    require(js.find("requestAnimationFrame") == std::string::npos);
}

FRAMEWORK_TEST(parse_react_native_export_runtime_bundle_materializes_with_host_react_shim) {
    // The vendored QuickJS trips an undefined-behavior trap in this runtime
    // shim path under UBSan only, so the body is skipped there.
#if defined(__has_feature)
#  if __has_feature(undefined_behavior_sanitizer)
    return;
#  endif
#endif

    const auto primary = read_fixture("rn_gain-stage.tsx");
    auto bundle = parse_react_native_export(primary);
    require(bundle.has_value());
    const auto runtime_js = asset_text(bundle->assets.front());
    maybe_write_rn_runtime_script(runtime_js);

    vellum::state::StateStore store;
    ScriptEngine engine;
    View root;
    WidgetBridge bridge(engine, root, store);

    bridge.load_script(minimal_host_react_dom_shim());
    bridge.load_script(runtime_js);
    bridge.service_frame_callbacks();

    require(root.child_count() > 0);
    require(engine.evaluate("!!document.getElementById('rn-gain-stage')")
                .getWithDefault<bool>(false));
    require(engine.evaluate(
        "(function(){ var el = document.getElementById('rn-gain-stage');"
        "return el && el.getAttribute('data-rn-default-flex') === 'column'; })()")
        .getWithDefault<bool>(false));
}

FRAMEWORK_TEST(parse_react_native_export_accepts_sanitized_rn_tsx) {
    const std::string sanitized = R"(
        import React, {
          useState
        } from "react";
        import {
          Pressable,
          StyleSheet,
          Text,
          View
        } from "react-native";
        export default function MultiLineNativePanel() {
          const [armed, setArmed] = useState(true);
          return (
            <View id="rn-multi-line-react-import" style={styles.panel}>
              <Text style={styles.label}>React Native export</Text>
              <Text style={styles.title}>Gain Stage</Text>
              <Pressable onPress={() => setArmed(!armed)} style={styles.button}>
                <Text>{armed ? "ARMED" : "BYPASS"}</Text>
              </Pressable>
            </View>
          );
        }
        const styles = StyleSheet.create({
          panel: { padding: 18, backgroundColor: "#111827" },
          label: { color: "#8fb3ff" },
          title: { color: "#f8fafc", fontSize: 24 },
          button: { minHeight: 36 }
        });
    )";

    auto bundle = parse_react_native_export(sanitized);
    require(bundle.has_value());
    require(bundle->template_html.find("rn-multi-line-react-import") != std::string::npos);
    const auto js = asset_text(bundle->assets.front());
    require(js.find("rn-multi-line-react-import") != std::string::npos);
    require(js.find("flexDirection: 'column'") != std::string::npos);
}

FRAMEWORK_TEST(parse_react_native_export_rejects_out_of_matrix_rn_surfaces) {
    const std::vector<std::string> rejected = {
        R"(
            import { useState } from "react";
            export default function GenericReactPanel() {
              return <div id="generic">Generic</div>;
            }
        )",
        R"(
            "use client";
            import { View } from "react-native";
            export default function SolitoPanel() {
              return <View />;
            }
        )",
        R"(
            import { Animated, View } from "react-native";
            export default function AnimatedPanel() {
              return <Animated.View />;
            }
        )",
        R"(
            import Animated from "react-native-reanimated";
            import { View } from "react-native";
            export default function WorkletPanel() {
              return <View />;
            }
        )",
        R"(
            import { Linking, View } from "react-native";
            export default function LinkingPanel() {
              Linking.openURL("https://example.com");
              return <View />;
            }
        )",
        R"(
            import { Alert, View } from "react-native";
            export default function AlertPanel() {
              Alert.alert("Nope");
              return <View />;
            }
        )",
        R"(
            import { Dimensions, View } from "react-native";
            export default function DimensionsPanel() {
              const width = Dimensions.get("window").width;
              return <View />;
            }
        )",
        R"(
            import { Platform, View } from "react-native";
            export default function PlatformPanel() {
              return <View />;
            }
        )",
        R"(
            import { Animated, View } from "react-native";
            export default function AnimatedImportPanel() {
              return <View />;
            }
        )",
        R"(
            import ReactNative from "react-native";
            export default function DefaultNativePanel() {
              return <ReactNative.View />;
            }
        )",
        R"(
            import * as ReactNative from "react-native";
            export default function NamespaceNativePanel() {
              return <ReactNative.View />;
            }
        )",
        R"(
            import { FlatList } from "react-native";
            export default function ListPanel() {
              return <FlatList data={[]} renderItem={() => null} />;
            }
        )",
        R"(
            import { Image } from "react-native";
            export default function ImagePanel() {
              return <Image source={{ uri: "https://example.com/a.png" }} />;
            }
        )",
        R"(
            import { View } from "react-native";
            export default function StyleArrayPanel() {
              return <View style={[styles.base, styles.hot]} />;
            }
            const styles = { base: {}, hot: {} };
        )",
        R"(
            import { View } from "react-native";
            import { NavigationContainer } from "@react-navigation/native";
            export default function NavigationPanel() {
              return <View />;
            }
        )",
        R"(
            import { View } from "react-native";
            export default function DomPanel() {
              return <div id="dom">DOM</div>;
            }
        )"
    };

    for (const auto& sample : rejected) {
        require(!parse_react_native_export(sample).has_value());
    }
}

// ── Pencil React export parsing ─────────────────────────────────────────

FRAMEWORK_TEST(parse_pencil_react_parses_staged_pencil_runtime_fixture) {
    const auto primary = read_fixture("pencil_gain-stage-card.tsx");
    auto bundle = parse_pencil_react(primary);
    require(bundle.has_value());
    require(bundle->assets.size() == 1);
    require(bundle->javascript_indices.size() == 1);
    require(bundle->javascript_indices.front() == 0);
    require(bundle->assets.front().uuid == "pencil-runtime-app");
    require(bundle->assets.front().mime == "text/javascript");
    require(bundle->template_html.find("data-pulp-source=\"pencil\"") != std::string::npos);
    require(bundle->template_html.find("pencil-gain-stage-card") != std::string::npos);

    const auto js = asset_text(bundle->assets.front());
    require(js.find("pencil-gain-stage-card") != std::string::npos);
    require(js.find("Pencil runtime import requires host React and ReactDOM") != std::string::npos);
    require(js.find("'data-pulp-source': 'pencil'") != std::string::npos);
    require(js.find("ReactDOM.createRoot") != std::string::npos);
    require(js.find("requestAnimationFrame") != std::string::npos);
    require(js.find("canvas.getContext('2d')") != std::string::npos);
    require(js.find("type: 'range'") != std::string::npos);
}

FRAMEWORK_TEST(parse_pencil_react_runtime_bundle_materializes_with_host_react_shim) {
    const auto primary = read_fixture("pencil_gain-stage-card.tsx");
    auto bundle = parse_pencil_react(primary);
    require(bundle.has_value());
    const auto runtime_js = asset_text(bundle->assets.front());
    maybe_write_pencil_runtime_script(runtime_js);

    vellum::state::StateStore store;
    ScriptEngine engine;
    View root;
    WidgetBridge bridge(engine, root, store);

    bridge.load_script(minimal_host_react_dom_shim());
    bridge.load_script(runtime_js);
    bridge.service_frame_callbacks();

    require(root.child_count() > 0);
    require(engine.evaluate("!!document.getElementById('pencil-gain-stage-card')")
                .getWithDefault<bool>(false));
    require(!engine.evaluate(
        "(function(){ var el = document.getElementById('pencil-gain-stage-card');"
        "return el ? String(el._id || '') : ''; })()").toString().empty());
}

FRAMEWORK_TEST(parse_pencil_react_accepts_sanitized_pencil_tsx) {
    const std::string sanitized = R"(
        import {
          useState
        } from "react";
        export default function MultiLinePencilPanel() {
          const [level, setLevel] = useState(0.5);
          return (
            <div id="pencil-multi-line-react-import" data-pencil-export="tailwind-jsx-sanitized" style={{ display: "flex", flexDirection: "column" }}>
              <span>Level</span>
              <input type="range" value={level} onChange={(event) => setLevel(Number(event.currentTarget.value))} />
            </div>
          );
        }
    )";

    auto bundle = parse_pencil_react(sanitized);
    require(bundle.has_value());
    require(bundle->template_html.find("pencil-multi-line-react-import") != std::string::npos);
    require(asset_text(bundle->assets.front()).find("pencil-multi-line-react-import") != std::string::npos);
    require(!parse_v0_dev_react(sanitized).has_value());

    const std::string no_id = R"(
        import { useState } from "react";
        export default function Meter_Panel() {
          const [level, setLevel] = useState(0.5);
          return (
            <div data-pencil-export="tailwind-jsx-sanitized" style={{ display: "flex", flexDirection: "column" }}>
              <span>Meter</span>
              <input type="range" value={level} onChange={(event) => setLevel(Number(event.currentTarget.value))} />
            </div>
          );
        }
    )";

    auto fallback_bundle = parse_pencil_react(no_id);
    require(fallback_bundle.has_value());
    require(fallback_bundle->template_html.find("pencil-meter-panel") != std::string::npos);
    require(asset_text(fallback_bundle->assets.front()).find("pencil-meter-panel") != std::string::npos);
}

FRAMEWORK_TEST(parse_pencil_react_rejects_out_of_matrix_pencil_defaults) {
    const std::vector<std::string> rejected = {
        R"(
            "use client";
            import { useState } from "react";
            export default function NextPencilPanel() {
              return <div id="pencil-next-panel">Next path</div>;
            }
        )",
        R"(
            import { useState } from "react";
            export default function TailwindPanel() {
              return <div className="flex rounded-lg bg-[--pencil-color-primary]">Tailwind</div>;
            }
        )",
        R"RAW(
            import { useState } from "react";
            export default function TokenPanel() {
              return <div id="pencil-token-panel" style={{ backgroundColor: "var(--pencil-color-primary)" }}>Token</div>;
            }
        )RAW",
        R"(
            import "./pencil.css";
            import { useState } from "react";
            export default function ExternalCssPanel() {
              return <div id="pencil-css-panel">CSS</div>;
            }
        )",
        R"(
            import { Dialog } from "@radix-ui/react-dialog";
            export default function RadixPanel() {
              return <div id="pencil-radix-panel">Radix</div>;
            }
        )",
        R"(
            import Link from "next/link";
            export default function NextImportPanel() {
              return <Link href="/">Next</Link>;
            }
        )",
        R"(
            import { View } from "react-native";
            export default function NativePanel() {
              return <View />;
            }
        )",
        R"({"mcp_response":{"node_tree":[],"source":"pencil"}})",
        R"({"batch_get":[{"id":"node-1"}]})",
        R"(
            import design from "./control.pen";
            export default function BinaryPenPanel() {
              return <div id="pencil-pen-panel">Binary</div>;
            }
        )",
        R"(
            import { useState } from "react";
            export default function TextInputPanel() {
              const [value, setValue] = useState("");
              return <input type="text" value={value} onChange={(event) => setValue(event.currentTarget.value)} />;
            }
        )",
        R"(
            import { useState } from "react";
            export default function CustomComponentPanel() {
              return <PencilWidget value={0.5} />;
            }
        )"
    };

    for (const auto& sample : rejected) {
        require(!parse_pencil_react(sample).has_value());
    }
}

int main() { return framework_test::run_all(); }
