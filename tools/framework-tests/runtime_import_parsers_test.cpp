// Checks the Stitch and React Native runtime-import parsers: each parses its
// fixture into a runtime bundle, the bundle materializes through a minimal host
// React shim, sanitized TSX is accepted, and out-of-matrix sources are refused.
// The widget bridge's __pulpRuntimeImport__ routes each source label to its
// parser, and a source its labelled parser refuses fails with that parser's message.

#include "framework_test.hpp"

#include <vellum/state/store.hpp>
#include <vellum/view/design_import.hpp>
#include <vellum/view/design_sources.hpp>
#include <vellum/view/js_engine.hpp>
#include <vellum/view/script_engine.hpp>
#include <vellum/view/widget_bridge.hpp>

#include <cstdio>
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

using namespace vellum::view;
using vellum::state::StateStore;

namespace {

// A failed prerequisite aborts the case before the test uses its result.
#define require(expression) \
    do { \
        if (!(expression)) throw std::runtime_error("Required: " #expression); \
    } while (false)

std::string read_fixture(const std::string& relative) {
    const std::string path = std::string(VELLUM_REPO_ROOT) + "/fixtures/runtime-import/" + relative;
    std::ifstream in(path, std::ios::binary);
    if (!in.good()) throw std::runtime_error("cannot read fixture " + path);
    std::ostringstream ss;
    ss << in.rdbuf();
    return ss.str();
}

std::string asset_text(const ClaudeBundleAsset& asset) {
    return std::string(asset.data.begin(), asset.data.end());
}

bool contains(const std::string& text, const std::string& needle) {
    return text.find(needle) != std::string::npos;
}

std::string js_single_quoted(const std::string& value) {
    std::string out;
    out.reserve(value.size() + 8);
    for (char c : value) {
        switch (c) {
            case '\\': out += "\\\\"; break;
            case '\'': out += "\\'"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            default: out += c; break;
        }
    }
    return out;
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


// Loads the shim and a runtime bundle into a fresh bridge and reports whether
// the element with `id` was mounted.
bool materializes(const std::string& runtime_js, const std::string& id,
                  const std::string& extra_check = {}) {
    ScriptEngine engine(JsEngineType::quickjs);
    View root;
    StateStore store;
    WidgetBridge bridge(engine, root, store);
    bridge.load_script(minimal_host_react_dom_shim());
    bridge.load_script(runtime_js);
    bridge.service_frame_callbacks();
    if (root.child_count() == 0) return false;
    if (!engine.evaluate("!!document.getElementById('" + id + "')").getWithDefault<bool>(false))
        return false;
    return extra_check.empty() || engine.evaluate(extra_check).getWithDefault<bool>(false);
}

// Routes `tsx` through __pulpRuntimeImport__ with `label` and returns the
// recorded error text.
std::string dispatch_error(const std::string& tsx, const std::string& label) {
    ScriptEngine engine(JsEngineType::quickjs);
    View root;
    StateStore store;
    WidgetBridge bridge(engine, root, store);
    bridge.install_runtime_import_handlers();
    engine.evaluate(
        "globalThis.__pulpRuntimeImportErr__ = null;"
        "try { __pulpRuntimeImport__('" + js_single_quoted(tsx) + "', '" + label + "'); }"
        "catch (e) { globalThis.__pulpRuntimeImportErr__ = 'threw:' + String(e); }");
    return engine.evaluate("String(globalThis.__pulpRuntimeImportErr__ || '')")
        .getWithDefault<std::string>("");
}

} // namespace

FRAMEWORK_TEST(stitch_parses_the_transport_bar_fixture) {
    auto bundle = parse_stitch_react(read_fixture("stitch/transport-bar.tsx"));
    require(bundle.has_value());
    CHECK(bundle->assets.size() == 1);
    CHECK(bundle->javascript_indices.size() == 1 && bundle->javascript_indices.front() == 0);
    CHECK(bundle->assets.front().uuid == "stitch-runtime-app");
    CHECK(bundle->assets.front().mime == "text/javascript");
    CHECK(contains(bundle->template_html, "data-pulp-source=\"stitch\""));
    CHECK(contains(bundle->template_html, "stitch-transport-bar"));
    const auto js = asset_text(bundle->assets.front());
    CHECK(contains(js, "stitch-transport-bar"));
    CHECK(contains(js, "Stitch runtime import requires host React and ReactDOM"));
    CHECK(contains(js, "'data-pulp-source': 'stitch'"));
    CHECK(contains(js, "ReactDOM.createRoot"));
    CHECK(contains(js, "requestAnimationFrame"));
    CHECK(contains(js, "performance.now"));
    CHECK(contains(js, "canvas.getContext('2d')"));
    CHECK(contains(js, "type: 'range'"));
}

FRAMEWORK_TEST(stitch_bundle_materializes_with_a_host_react_shim) {
    auto bundle = parse_stitch_react(read_fixture("stitch/transport-bar.tsx"));
    require(bundle.has_value());
    CHECK(materializes(asset_text(bundle->assets.front()), "stitch-transport-bar",
        "(function(){ var el = document.getElementById('stitch-transport-bar');"
        "return !!el && String(el._id || '') !== ''; })()"));
}

FRAMEWORK_TEST(stitch_accepts_sanitized_tsx) {
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
    CHECK(contains(bundle->template_html, "stitch-multi-line-react-import"));
    CHECK(contains(asset_text(bundle->assets.front()), "stitch-multi-line-react-import"));
}

FRAMEWORK_TEST(stitch_refuses_out_of_matrix_sources) {
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

    for (std::size_t i = 0; i < rejected.size(); ++i) {
        if (parse_stitch_react(rejected[i]).has_value()) {
            std::fprintf(stderr, "  accepted out-of-matrix Stitch sample %zu\n", i);
            CHECK(false);
        }
    }
}

FRAMEWORK_TEST(react_native_parses_the_gain_stage_fixture) {
    auto bundle = parse_react_native_export(read_fixture("rn/gain-stage.tsx"));
    require(bundle.has_value());
    CHECK(bundle->assets.size() == 1);
    CHECK(bundle->javascript_indices.size() == 1 && bundle->javascript_indices.front() == 0);
    CHECK(bundle->assets.front().uuid == "rn-runtime-app");
    CHECK(bundle->assets.front().mime == "text/javascript");
    CHECK(contains(bundle->template_html, "data-pulp-source=\"rn\""));
    CHECK(contains(bundle->template_html, "rn-gain-stage"));
    const auto js = asset_text(bundle->assets.front());
    CHECK(contains(js, "rn-gain-stage"));
    CHECK(contains(js, "React Native runtime import requires host React and ReactDOM"));
    CHECK(contains(js, "'data-pulp-source': 'rn'"));
    CHECK(contains(js, "'data-rn-default-flex': 'column'"));
    CHECK(contains(js, "flexDirection: 'column'"));
    CHECK(contains(js, "ReactDOM.createRoot"));
    CHECK(!contains(js, "canvas.getContext"));
    CHECK(!contains(js, "requestAnimationFrame"));
}

FRAMEWORK_TEST(react_native_bundle_materializes_with_a_host_react_shim) {
    auto bundle = parse_react_native_export(read_fixture("rn/gain-stage.tsx"));
    require(bundle.has_value());
    CHECK(materializes(asset_text(bundle->assets.front()), "rn-gain-stage",
        "(function(){ var el = document.getElementById('rn-gain-stage');"
        "return !!el && el.getAttribute('data-rn-default-flex') === 'column'; })()"));
}

FRAMEWORK_TEST(react_native_accepts_sanitized_tsx) {
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
    CHECK(contains(bundle->template_html, "rn-multi-line-react-import"));
    const auto js = asset_text(bundle->assets.front());
    CHECK(contains(js, "rn-multi-line-react-import"));
    CHECK(contains(js, "flexDirection: 'column'"));
}

FRAMEWORK_TEST(react_native_refuses_out_of_matrix_sources) {
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

    for (std::size_t i = 0; i < rejected.size(); ++i) {
        if (parse_react_native_export(rejected[i]).has_value()) {
            std::fprintf(stderr, "  accepted out-of-matrix React Native sample %zu\n", i);
            CHECK(false);
        }
    }
}

FRAMEWORK_TEST(runtime_import_routes_stitch_by_source_label) {
    const std::string tsx = R"(
        import { useState } from "react";
        export default function DispatchProbe() {
          const [level, setLevel] = useState(0.5);
          return (
            <div id="stitch-dispatch-probe" data-stitch-screen="probe" style={{ display: "flex", flexDirection: "column" }}>
              <span>Level</span>
              <input type="range" min={0} max={1} step={0.01} value={level}
                onChange={(event) => setLevel(Number(event.currentTarget.value))} />
            </div>
          );
        }
    )";
    const auto error = dispatch_error(tsx, "stitch");
    CHECK(!contains(error, "threw:"));
    CHECK(!contains(error, "claude bundle"));
    CHECK(contains(error, "Stitch runtime import requires host React and ReactDOM"));
}

FRAMEWORK_TEST(runtime_import_routes_react_native_by_source_label) {
    const std::string tsx = R"(
        import React, { useState } from "react";
        import { Pressable, StyleSheet, Text, View } from "react-native";
        export default function DispatchProbe() {
          const [armed, setArmed] = useState(true);
          return (
            <View id="rn-dispatch-probe" style={styles.panel}>
              <Text>React Native export</Text>
              <Pressable onPress={() => setArmed(!armed)} style={styles.button}>
                <Text>{armed ? "ARMED" : "BYPASS"}</Text>
              </Pressable>
            </View>
          );
        }
        const styles = StyleSheet.create({
          panel: { padding: 18, backgroundColor: "#111827" },
          button: { minHeight: 36 }
        });
    )";
    const auto error = dispatch_error(tsx, "rn");
    CHECK(!contains(error, "threw:"));
    CHECK(!contains(error, "claude bundle"));
    CHECK(contains(error, "React Native runtime import requires host React and ReactDOM"));
}

FRAMEWORK_TEST(runtime_import_label_refusals_name_their_parser) {
    // A source the labelled parser refuses must fail with that parser's own
    // message, not fall through to another parser or the generic envelope error.
    const std::string dom_panel =
        "import { useState } from \"react\";\n"
        "export default function DomPanel() { return <div id=\"dom\">DOM</div>; }\n";
    const std::string native_panel =
        "import { View } from \"react-native\";\n"
        "export default function NativePanel() { return <View />; }\n";
    CHECK(contains(dispatch_error(dom_panel, "rn"), "unsupported React Native export"));
    CHECK(contains(dispatch_error(native_panel, "stitch"), "unsupported Stitch React export"));
}

int main() { return framework_test::run_all(); }
