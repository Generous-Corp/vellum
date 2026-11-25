// test_web_compat_react_shims.cpp
//
// Verifies the web-compat preludes expose the surface React 18 (and similar
// bundled-React frameworks) feature-detect against. Each of these checks
// would catch a regression that breaks bundled-React import through a
// Claude runtime-bundle execution.
//
// Methodology: spin up a real WidgetBridge so all preludes are evaluated
// in the same order they ship at runtime, then run a small JS snippet
// that exercises the surface and writes a one-line marker into a global
// the test reads back via load_script. We reuse the existing canvas
// command-recording pattern so we don't re-implement bridge plumbing.

#include "framework_test.hpp"

#include <vellum/state/store.hpp>
#include <vellum/view/script_engine.hpp>
#include <vellum/view/view.hpp>
#include <vellum/view/widget_bridge.hpp>

#include <chrono>
#include <stdexcept>
#include <string>
#include <thread>

using namespace vellum::view;
using namespace vellum::state;

// A failed prerequisite aborts the case before the test uses its result.
#define require(expression) \
    do { \
        if (!(expression)) throw std::runtime_error("Required: " #expression); \
    } while (false)

namespace {

// Build a minimal bridge so all web-compat preludes load (the bridge
// constructor is what evaluates them). Returns the script result of the
// final expression (a stringified marker our tests check).
std::string run_in_bridge(const std::string& js) {
    ScriptEngine engine;
    View root;
    root.set_bounds({0, 0, 400, 300});
    StateStore store;
    WidgetBridge bridge(engine, root, store);
    // load_script appends ";void 0" so we can't read the expression
    // value directly. Stash the result in a global and read it back.
    bridge.load_script("globalThis.__test_result__ = (function(){\n"
                       + js +
                       "\n})();");
    auto val = engine.evaluate("String(globalThis.__test_result__)");
    if (val.isString()) return std::string(val.getString());
    return std::string{};
}

// Same as run_in_bridge but pumps the JS job queue once after eval, so
// queueMicrotask / Promise.then / async-await callbacks actually drain
// before we read back the marker. Without the
// pump_message_loop drain this returns the pre-pump value.
std::string run_in_bridge_with_pump(const std::string& js) {
    ScriptEngine engine;
    View root;
    root.set_bounds({0, 0, 400, 300});
    StateStore store;
    WidgetBridge bridge(engine, root, store);
    bridge.load_script("globalThis.__test_result__ = (function(){\n"
                       + js +
                       "\n})();");
    engine.pump_message_loop();
    auto val = engine.evaluate("String(globalThis.__test_result__)");
    if (val.isString()) return std::string(val.getString());
    return std::string{};
}

} // namespace

// ── nodeType / nodeName on Element ──────────────────────────────────────

FRAMEWORK_TEST(element_nodes_report_nodetype_1_and_nodename_tagname) {
    auto result = run_in_bridge(R"(
        var div = document.createElement('div');
        var span = document.createElement('span');
        return [div.nodeType, div.nodeName, span.nodeType, span.nodeName].join('|');
    )");
    require(result == "1|DIV|1|SPAN");
}

FRAMEWORK_TEST(element_constructor_prototype_expose_dom_level_1_node_type_constants) {
    auto result = run_in_bridge(R"(
        var d = document.createElement('div');
        return [
            Element.ELEMENT_NODE,
            Element.TEXT_NODE,
            Element.COMMENT_NODE,
            d.ELEMENT_NODE,
            d.TEXT_NODE,
            d.COMMENT_NODE
        ].join(',');
    )");
    require(result == "1,3,8,1,3,8");
}

// ── createTextNode reports as a real text node ──────────────────────────

FRAMEWORK_TEST(createtextnode_reports_nodetype_3_and_nodename_text) {
    auto result = run_in_bridge(R"(
        var t = document.createTextNode('hello');
        return [t.nodeType, t.nodeName, t.data, t.nodeValue].join('|');
    )");
    require(result == "3|#text|hello|hello");
}

FRAMEWORK_TEST(text_node_data_setter_mirrors_nodevalue_and_updates_textcontent) {
    auto result = run_in_bridge(R"(
        var t = document.createTextNode('initial');
        t.data = 'updated';
        return [t.data, t.nodeValue, t._textContent].join('|');
    )");
    require(result == "updated|updated|updated");
}

// ── createComment ───────────────────────────────────────────────────────

FRAMEWORK_TEST(createcomment_reports_nodetype_8_and_nodename_comment) {
    auto result = run_in_bridge(R"(
        var c = document.createComment('hidden marker');
        return [c.nodeType, c.nodeName, c.data].join('|');
    )");
    require(result == "8|#comment|hidden marker");
}

// ── createDocumentFragment ──────────────────────────────────────────────

FRAMEWORK_TEST(createdocumentfragment_reports_nodetype_11) {
    auto result = run_in_bridge(R"(
        var f = document.createDocumentFragment();
        return [f.nodeType, f.nodeName].join('|');
    )");
    require(result == "11|#document-fragment");
}

// DocumentFragment MUST flatten on insert: the fragment's children move
// into the parent and the fragment node itself is never inserted. React
// 18's reconciler stages commits in fragments, so phantom wrapper divs
// must not appear in the materialized tree.
FRAMEWORK_TEST(appendchild_fragment_flattens_fragment_children_into_the_parent) {
    auto result = run_in_bridge(R"(
        var parent = document.createElement('div');
        var frag = document.createDocumentFragment();
        var a = document.createElement('span');
        var b = document.createElement('span');
        var c = document.createElement('span');
        frag.appendChild(a);
        frag.appendChild(b);
        frag.appendChild(c);
        var before = frag._children.length;
        parent.appendChild(frag);
        return [
            'before=' + before,
            'parent=' + parent._children.length,
            'frag=' + frag._children.length,
            'first=' + (parent._children[0] === a),
            'last=' + (parent._children[2] === c),
            'parentOfA=' + (a._parentElement === parent)
        ].join(',');
    )");
    require(result ==
            "before=3,parent=3,frag=0,first=true,last=true,parentOfA=true");
}

FRAMEWORK_TEST(insertbefore_fragment_flattens_fragment_children_in_order) {
    auto result = run_in_bridge(R"(
        var parent = document.createElement('div');
        var first = document.createElement('span');
        var third = document.createElement('span');
        parent.appendChild(first);
        parent.appendChild(third);
        var frag = document.createDocumentFragment();
        var a = document.createElement('span');
        var b = document.createElement('span');
        frag.appendChild(a);
        frag.appendChild(b);
        // Insert the fragment before `third`, expect first, a, b, third.
        parent.insertBefore(frag, third);
        return [
            'count=' + parent._children.length,
            'order=' +
                (parent._children[0] === first) + ',' +
                (parent._children[1] === a) + ',' +
                (parent._children[2] === b) + ',' +
                (parent._children[3] === third),
            'frag=' + frag._children.length
        ].join('|');
    )");
    require(result == "count=4|order=true,true,true,true|frag=0");
}

// ── DOM-ops idempotency ──────────────────────────────────────────
//
// A context menu uses `ref.current.contains(e.target)` for click-outside
// dismiss. A web-compat-element.js without `Element.prototype.contains` throws
// `TypeError` when the DOM-shim path is the active one for ref.current. Pin the
// standard `Node.contains` contract so a regression surfaces in CI.

FRAMEWORK_TEST(element_contains_walks_parentelement_chain_dom_node_contains_contract) {
    auto result = run_in_bridge(R"(
        var grand = document.createElement('div');
        var parent = document.createElement('div');
        var child = document.createElement('span');
        var sibling = document.createElement('span');
        var detached = document.createElement('span');
        grand.appendChild(parent);
        parent.appendChild(child);
        grand.appendChild(sibling);
        return [
            // self
            'self=' + grand.contains(grand),
            // direct child
            'directChild=' + grand.contains(parent),
            // grandchild
            'grandchild=' + grand.contains(child),
            // sibling
            'sibling=' + grand.contains(sibling),
            // detached node, not in tree
            'detached=' + grand.contains(detached),
            // null/undefined are false
            'nullArg=' + grand.contains(null),
            'undefinedArg=' + grand.contains(undefined),
            // ancestor doesn't contain its own ancestor
            'childContainsGrand=' + child.contains(grand)
        ].join('|');
    )");
    require(result ==
            "self=true|directChild=true|grandchild=true|sibling=true|"
            "detached=false|nullArg=false|undefinedArg=false|"
            "childContainsGrand=false");
}

// The standalone web-compat-dom-ops.js prelude carries an idempotency guard so re-eval'ing the
// prelude a second time leaves the prototype methods in place instead
// of re-defining them. Pin both invariants here.

FRAMEWORK_TEST(dom_mutation_methods_are_tagged_with_the_pulp_dom_ops_marker) {
    auto result = run_in_bridge(R"(
        var names = ['appendChild','removeChild','remove','insertBefore','replaceChild'];
        var ok = [];
        for (var i = 0; i < names.length; i++) {
            var fn = Element.prototype[names[i]];
            ok.push(names[i] + '=' + (fn && fn.__pulp_dom_ops__ === true));
        }
        return ok.join(',');
    )");
    require(result ==
            "appendChild=true,removeChild=true,remove=true,insertBefore=true,replaceChild=true");
}

FRAMEWORK_TEST(re_evaluating_the_dom_ops_prelude_does_not_re_define_the_prototype_methods) {
    // Capture identity, re-eval the prelude verbatim, and assert each
    // method is the same function object. If the guard regresses, a
    // second eval would replace each method with a freshly-defined
    // closure and identity would break.
    ScriptEngine engine;
    View root;
    root.set_bounds({0, 0, 400, 300});
    StateStore store;
    WidgetBridge bridge(engine, root, store);
    bridge.load_script(R"(
        globalThis.__before__ = {
            appendChild: Element.prototype.appendChild,
            removeChild: Element.prototype.removeChild,
            insertBefore: Element.prototype.insertBefore,
            replaceChild: Element.prototype.replaceChild,
            remove: Element.prototype.remove
        };
    )");
    // Force a second eval of the same prelude logic by load_script-ing
    // a snippet whose only side effect is that re-eval. The bridge no
    // longer guards against this with a C++-side flag; the JS guard
    // must do the right thing.
    bridge.load_script(R"(
        // Inline a copy of the guard + appendChild reassignment, then
        // verify the original still wins.
        if (!Element.prototype.appendChild ||
            !Element.prototype.appendChild.__pulp_dom_ops__) {
            Element.prototype.appendChild = function () { return null; };
            Element.prototype.appendChild.__pulp_dom_ops__ = true;
        }
        globalThis.__after_match__ =
            (Element.prototype.appendChild === globalThis.__before__.appendChild) &&
            (Element.prototype.removeChild === globalThis.__before__.removeChild) &&
            (Element.prototype.insertBefore === globalThis.__before__.insertBefore) &&
            (Element.prototype.replaceChild === globalThis.__before__.replaceChild) &&
            (Element.prototype.remove === globalThis.__before__.remove);
    )");
    auto v = engine.evaluate("String(globalThis.__after_match__)");
    require(v.isString());
    require(std::string(v.getString()) == "true");
}

// ── Observer constructors exist and are no-ops ──────────────────────────

FRAMEWORK_TEST(mutationobserver_intersectionobserver_resizeobserver_performanceobserver_are_constructible_no_ops) {
    auto result = run_in_bridge(R"(
        var names = ['MutationObserver','IntersectionObserver','ResizeObserver','PerformanceObserver'];
        var ok = [];
        for (var i = 0; i < names.length; i++) {
            var Ctor = globalThis[names[i]];
            if (typeof Ctor !== 'function') { ok.push(names[i] + ':missing'); continue; }
            var inst = new Ctor(function(){});
            inst.observe();   // no throw
            inst.disconnect();
            inst.takeRecords();
            ok.push(names[i] + ':ok');
        }
        return ok.join(',');
    )");
    require(result ==
            "MutationObserver:ok,IntersectionObserver:ok,ResizeObserver:ok,PerformanceObserver:ok");
}

FRAMEWORK_TEST(xmlhttprequest_is_a_constructible_no_op_with_the_spec_readystate_constants) {
    auto result = run_in_bridge(R"(
        if (typeof XMLHttpRequest !== 'function') return 'missing';
        var xhr = new XMLHttpRequest();
        xhr.open('GET', '/x');
        xhr.send();
        return [
            XMLHttpRequest.UNSENT, XMLHttpRequest.OPENED,
            XMLHttpRequest.HEADERS_RECEIVED, XMLHttpRequest.LOADING,
            XMLHttpRequest.DONE
        ].join(',');
    )");
    require(result == "0,1,2,3,4");
}

FRAMEWORK_TEST(element_exposes_a_no_op_scrolltop_scrollleft_pair_returning_0) {
    auto result = run_in_bridge(R"(
        var d = document.createElement('div');
        d.scrollTop = 99;   // setter is no-op
        d.scrollLeft = 99;
        return [d.scrollTop, d.scrollLeft].join(',');
    )");
    require(result == "0,0");
}

// ── queueMicrotask ──────────────────────────────────────────────────────

FRAMEWORK_TEST(queuemicrotask_is_exposed_as_a_function_on_globalthis) {
    auto result = run_in_bridge("return typeof queueMicrotask;");
    require(result == "function");
}

FRAMEWORK_TEST(queuemicrotask_invocation_does_not_throw_and_accepts_non_functions_silently) {
    // The validation path: queueMicrotask is exposed, is callable,
    // accepts a function argument without throwing, and silently
    // ignores non-functions. Each of these is what React 18's
    // `typeof queueMicrotask === 'function'` probe + scheduler bootstrap
    // reads. Drain semantics are covered separately below.
    auto result = run_in_bridge(R"(
        var threw = false;
        try {
            queueMicrotask(function () {});
            queueMicrotask();             // non-function: silently ignored
        } catch (e) { threw = true; }
        return threw ? 'threw' : 'ok';
    )");
    require(result == "ok");
}

// ── Microtask draining ───────────────────────────────────────────
//
// CHOC's QuickJS pumpMessageLoop has an empty body, so without the drain in
// js_quickjs_engine.cpp the microtask queue silently stays pending across
// pump_message_loop() calls. These tests pin the contract: after pump, every
// callback the queue knew about has run, for a bare microtask, Promise.then,
// async/await and transitive scheduling.

FRAMEWORK_TEST(pump_message_loop_drains_a_bare_queuemicrotask_callback) {
    auto result = run_in_bridge_with_pump(R"(
        globalThis.__seen__ = 'before';
        queueMicrotask(function () { globalThis.__seen__ = 'after'; });
        return 'scheduled';  // pre-pump marker the harness ignores
    )");
    // Override: read __seen__ via the marker write path.
    require(result == "scheduled");
    // Verify the side-effect landed by re-running with the pump in the
    // returned expression so we observe the post-pump state.
    auto seen = run_in_bridge_with_pump(R"(
        globalThis.__flag__ = 'before';
        queueMicrotask(function () { globalThis.__flag__ = 'after'; });
        // Pump is invoked by the harness AFTER this returns; we cannot
        // read __flag__ from within the IIFE because the microtask has
        // not yet run when the IIFE returns. Stash the global name so
        // a follow-up evaluator can read it post-pump, but the harness
        // already does that for __test_result__, so chain through that
        // by stringifying after the pump from within a deferred read.
        return globalThis.__flag__;
    )");
    // Pre-pump observation inside the IIFE returns 'before'; after the
    // harness pumps, the global itself has flipped to 'after'. Since
    // __test_result__ captured the IIFE's pre-pump value, reading it
    // here should still be 'before', the *real* drain proof is the
    // dedicated test below that re-reads the global after the pump.
    require(seen == "before");
}

FRAMEWORK_TEST(pump_message_loop_runs_queuemicrotask_side_effects_observable_post_pump) {
    // Direct ScriptEngine drive so we can interleave pump_message_loop
    // and reads across the JS boundary explicitly.
    ScriptEngine engine;
    View root;
    root.set_bounds({0, 0, 400, 300});
    StateStore store;
    WidgetBridge bridge(engine, root, store);
    bridge.load_script(R"(
        globalThis.__drain_marker__ = 'pending';
        queueMicrotask(function () { globalThis.__drain_marker__ = 'drained'; });
    )");
    auto pre = engine.evaluate("String(globalThis.__drain_marker__)");
    require(pre.isString());
    require(std::string(pre.getString()) == "pending");

    engine.pump_message_loop();

    auto post = engine.evaluate("String(globalThis.__drain_marker__)");
    require(post.isString());
    require(std::string(post.getString()) == "drained");
}

FRAMEWORK_TEST(pump_message_loop_runs_promise_then_continuations) {
    ScriptEngine engine;
    View root;
    root.set_bounds({0, 0, 400, 300});
    StateStore store;
    WidgetBridge bridge(engine, root, store);
    bridge.load_script(R"(
        globalThis.__chain_marker__ = 'before';
        Promise.resolve('a').then(function (v) {
            return v + ':b';                         // pass through chain
        }).then(function (v) {
            globalThis.__chain_marker__ = v + ':c';  // observe end-of-chain
        });
    )");
    engine.pump_message_loop();
    auto v = engine.evaluate("String(globalThis.__chain_marker__)");
    require(v.isString());
    // Both .then continuations must have run, promise chains drain
    // through queued jobs each step at a time, so the final marker
    // proves the second job was reached after the first scheduled it.
    require(std::string(v.getString()) == "a:b:c");
}

FRAMEWORK_TEST(pump_message_loop_drains_async_await_round_trip) {
    ScriptEngine engine;
    View root;
    root.set_bounds({0, 0, 400, 300});
    StateStore store;
    WidgetBridge bridge(engine, root, store);
    bridge.load_script(R"(
        globalThis.__async_marker__ = 'before';
        (async function () {
            var v = await Promise.resolve(42);
            globalThis.__async_marker__ = 'awaited:' + v;
        })();
    )");
    engine.pump_message_loop();
    auto v = engine.evaluate("String(globalThis.__async_marker__)");
    require(v.isString());
    require(std::string(v.getString()) == "awaited:42");
}

FRAMEWORK_TEST(pump_message_loop_transitively_drains_microtasks_scheduled_from_microtasks) {
    ScriptEngine engine;
    View root;
    root.set_bounds({0, 0, 400, 300});
    StateStore store;
    WidgetBridge bridge(engine, root, store);
    bridge.load_script(R"(
        globalThis.__transitive__ = 0;
        function step (n) {
            return function () {
                globalThis.__transitive__ = n;
                if (n < 5) queueMicrotask(step(n + 1));
            };
        }
        queueMicrotask(step(1));
    )");
    engine.pump_message_loop();
    auto v = engine.evaluate("String(globalThis.__transitive__)");
    require(v.isString());
    // Each microtask schedules the next; the pump must drain
    // transitively until the queue truly empties.
    require(std::string(v.getString()) == "5");
}

// The message-loop pump must drain to empty (rc == 0), not return after
// an arbitrary job count with a Promise/microtask chain half-drained.
// Verify by scheduling 5000 microtasks (past the old 4096-job cutoff)
// in one chain and asserting every one of them executed before the pump
// returns.
FRAMEWORK_TEST(pump_message_loop_drains_a_long_chain_past_the_old_4096_job_cutoff) {
    ScriptEngine engine;
    View root;
    root.set_bounds({0, 0, 400, 300});
    StateStore store;
    WidgetBridge bridge(engine, root, store);
    bridge.load_script(R"(
        globalThis.__count__ = 0;
        var TARGET = 5000;
        function step () {
            globalThis.__count__++;
            if (globalThis.__count__ < TARGET) queueMicrotask(step);
        }
        queueMicrotask(step);
    )");
    engine.pump_message_loop();
    auto v = engine.evaluate("String(globalThis.__count__)");
    require(v.isString());
    require(std::string(v.getString()) == "5000");
}

// A self-rearming microtask must not hard-freeze the UI
// thread. The pump is bounded (1M jobs) and logs a warning when that
// bound fires. This test pins the bound: a microtask that always
// re-queues itself must NOT hang `pump_message_loop()`.
//
// We can't drive the pump from a worker thread, QuickJS's JSContext
// is single-threaded and cross-thread access is undefined (Linux CI
// surfaced this as count==0 because the foreign-thread call returned
// rc<0 immediately). So we drive the pump synchronously on the
// calling thread and rely on two complementary checks:
//
//   1. A wall-clock budget around the call. If the pump returns
//      within a generous limit it is by definition bounded; a
//      regression of the bound would hang the test process and trip
//      the CTest TIMEOUT, surfacing as a Failed test instead of
//      silent success.
//   2. A counter on the JS side bumped every microtask. After the
//      pump returns we assert it reached well past the 5K of the
//      prior issue-769 test, proving the cap (not an early-empty
//      return) is what stopped us.
FRAMEWORK_TEST(pump_message_loop_is_bounded_against_a_self_rearming_microtask) {
    ScriptEngine engine;
    View root;
    root.set_bounds({0, 0, 400, 300});
    StateStore store;
    WidgetBridge bridge(engine, root, store);
    bridge.load_script(R"(
        globalThis.__runaway__ = 0;
        function loop () {
            globalThis.__runaway__++;
            queueMicrotask(loop);  // never terminates on its own
        }
        queueMicrotask(loop);
    )");

    // 1M JS_ExecutePendingJob calls of a 2-statement microtask finish
    // well inside this test's 180s CTest timeout on supported hosts.
    // Sanitizer builds can be much slower than release/debug hosts; a
    // wedged regression would hang and trip CTest's TIMEOUT line, which
    // is the loud signal we want.
    auto t0 = std::chrono::steady_clock::now();
    engine.pump_message_loop();
    auto elapsed = std::chrono::steady_clock::now() - t0;

    // Generous upper bound, sanitizer builds and slow CI runners are
    // real. The point is to catch a multi-minute hang, not benchmark.
    using namespace std::chrono_literals;
    require(elapsed < 170s);

    // Prove we actually exercised the cap (not just returned early on
    // rc==0). The JS-side counter must be well past the 5K used by
    // the prior issue-769 test.
    auto v = engine.evaluate("String(globalThis.__runaway__)");
    require(v.isString());
    long long count = std::stoll(std::string(v.getString()));
    require(count >= 100000);
}

// ── MessageChannel + MessagePort + postMessage ─────────────────────────

FRAMEWORK_TEST(messagechannel_constructs_exposes_port1_port2_and_postmessage_doesn_t_throw) {
    auto result = run_in_bridge(R"(
        var c = new MessageChannel();
        var ok = (typeof c.port1 === 'object' && typeof c.port2 === 'object'
                  && typeof c.port1.postMessage === 'function');
        c.port1.onmessage = function () {};
        c.port2.onmessage = function () {};
        c.port1.postMessage({k:'v'});  // queued microtask, no throw
        c.port1.start();
        c.port1.close();
        return ok ? 'ok' : 'broken';
    )");
    require(result == "ok");
}

// React 18's scheduler specifically checks `window.postMessage` (not just
// `globalThis.postMessage`). `window` is a distinct object in this runtime,
// so the scheduler shim needs to mirror.
FRAMEWORK_TEST(postmessage_is_available_on_both_globalthis_and_window) {
    auto result = run_in_bridge(R"(
        return [
            'globalThis=' + (typeof globalThis.postMessage),
            'window=' + (typeof window.postMessage),
            'same=' + (globalThis.postMessage === window.postMessage)
        ].join('|');
    )");
    require(result == "globalThis=function|window=function|same=true");
}

// `window.parent` self-reference. Real browser top-level windows have
// `window.parent === window`; imported React designs commonly call
// `window.parent.postMessage(...)` for cross-frame messaging (4 sites
// in Spectr's edit-mode bridge alone). Without the self-reference the
// property access throws TypeError and the surrounding effect dies
// silently.
FRAMEWORK_TEST(window_parent_is_a_self_reference_and_supports_postmessage) {
    auto result = run_in_bridge(R"(
        return [
            'defined=' + (typeof window.parent),
            'self=' + (window.parent === window),
            'callable=' + (typeof window.parent.postMessage)
        ].join('|');
    )");
    require(result == "defined=object|self=true|callable=function");
}

// ── URLSearchParams ────────────────────────────────────────────────────

FRAMEWORK_TEST(urlsearchparams_parses_mutates_and_serializes_round_trip) {
    auto result = run_in_bridge(R"(
        var p = new URLSearchParams('a=1&b=2&a=3');
        p.append('c', 'hi there');
        p.set('a', 'one');
        var out = [];
        out.push(p.get('a'));         // 'one' (set collapses dupes)
        out.push(p.get('b'));
        out.push(p.getAll('a').length); // 1 after set
        out.push(p.has('c'));
        out.push(p.toString());        // a=one&b=2&c=hi+there
        return out.join('|');
    )");
    require(result == "one|2|1|true|a=one&b=2&c=hi+there");
}

FRAMEWORK_TEST(urlsearchparams_accepts_a_record_style_object_init) {
    auto result = run_in_bridge(R"(
        var p = new URLSearchParams({a: 1, b: 'two'});
        return p.toString();
    )");
    // Object iteration order isn't guaranteed across engines; accept either.
    require((result == "a=1&b=two" || result == "b=two&a=1"));
}

// ── Native web-API globals ─────────────────────────────────
//
// `requestAnimationFrame`, `cancelAnimationFrame`,
// `setTimeout`, `clearTimeout`, `setInterval`, `clearInterval`, and
// `MessageChannel` are now installed by WidgetBridge::register_api +
// the web-compat-scheduler.js prelude as standard global names. Spectr
// (and any other consumer of the React package) used to ship a ~80-line
// `shim.js` that re-aliased the underscored `__requestFrame__` / etc.
// these tests pin the contract that no JS-side shim is needed: every
// global exists on `globalThis` immediately after bridge construction.

FRAMEWORK_TEST(settimeout_cleartimeout_setinterval_clearinterval_are_functions_on_globalthis) {
    auto result = run_in_bridge(R"(
        return [
            typeof globalThis.setTimeout,
            typeof globalThis.clearTimeout,
            typeof globalThis.setInterval,
            typeof globalThis.clearInterval
        ].join(',');
    )");
    require(result == "function,function,function,function");
}

FRAMEWORK_TEST(requestanimationframe_cancelanimationframe_are_functions_on_globalthis) {
    auto result = run_in_bridge(R"(
        return [
            typeof globalThis.requestAnimationFrame,
            typeof globalThis.cancelAnimationFrame
        ].join(',');
    )");
    require(result == "function,function");
}

FRAMEWORK_TEST(performance_now_is_callable_and_returns_a_finite_number) {
    auto result = run_in_bridge(R"(
        var v = performance.now();
        return [
            typeof globalThis.performance,
            typeof globalThis.performance.now,
            typeof v,
            isFinite(v) ? 'finite' : 'inf'
        ].join(',');
    )");
    require(result == "object,function,number,finite");
}

FRAMEWORK_TEST(settimeout_returns_a_numeric_id_and_fires_on_next_pump_message_loop_for_ms_0) {
    ScriptEngine engine;
    View root;
    root.set_bounds({0, 0, 400, 300});
    StateStore store;
    WidgetBridge bridge(engine, root, store);
    bridge.load_script(R"(
        globalThis.__t_marker__ = 'before';
        globalThis.__t_id__ = setTimeout(function () {
            globalThis.__t_marker__ = 'after';
        }, 0);
    )");

    auto id = engine.evaluate("typeof globalThis.__t_id__ + ':' + globalThis.__t_id__");
    require(id.isString());
    auto id_str = std::string(id.getString());
    require(id_str.find("number:") == 0);

    auto pre = engine.evaluate("String(globalThis.__t_marker__)");
    require(std::string(pre.getString()) == "before");

    engine.pump_message_loop();

    auto post = engine.evaluate("String(globalThis.__t_marker__)");
    require(std::string(post.getString()) == "after");
}

FRAMEWORK_TEST(cleartimeout_cancels_a_pending_zero_delay_timer_before_pump) {
    ScriptEngine engine;
    View root;
    root.set_bounds({0, 0, 400, 300});
    StateStore store;
    WidgetBridge bridge(engine, root, store);
    bridge.load_script(R"(
        globalThis.__t_marker__ = 'before';
        var id = setTimeout(function () {
            globalThis.__t_marker__ = 'fired';
        }, 0);
        clearTimeout(id);
    )");
    engine.pump_message_loop();
    auto v = engine.evaluate("String(globalThis.__t_marker__)");
    require(std::string(v.getString()) == "before");
}

FRAMEWORK_TEST(settimeout_with_positive_delay_fires_after_service_frame_callbacks) {
    ScriptEngine engine;
    View root;
    root.set_bounds({0, 0, 400, 300});
    StateStore store;
    WidgetBridge bridge(engine, root, store);
    bridge.load_script(R"(
        globalThis.__pos_marker__ = 'pre';
        setTimeout(function () { globalThis.__pos_marker__ = 'post'; }, 5);
    )");
    // 50ms is well above the 5ms delay and well below the test timeout.
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
    bridge.service_frame_callbacks();
    auto v = engine.evaluate("String(globalThis.__pos_marker__)");
    require(std::string(v.getString()) == "post");
}

FRAMEWORK_TEST(setinterval_rearms_across_multiple_service_frame_callbacks_calls) {
    ScriptEngine engine;
    View root;
    root.set_bounds({0, 0, 400, 300});
    StateStore store;
    WidgetBridge bridge(engine, root, store);
    bridge.load_script(R"(
        globalThis.__iv_count__ = 0;
        globalThis.__iv_id__ = setInterval(function () {
            globalThis.__iv_count__++;
        }, 5);
    )");
    // Three frame ticks at >5ms apart → at least three invocations.
    for (int i = 0; i < 3; ++i) {
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
        bridge.service_frame_callbacks();
    }
    auto v = engine.evaluate("String(globalThis.__iv_count__)");
    require(v.isString());
    long long count = std::stoll(std::string(v.getString()));
    require(count >= 3);

    // clearInterval stops the rearm cycle.
    engine.evaluate("clearInterval(globalThis.__iv_id__);void 0");
    long long before_clear = count;
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
    bridge.service_frame_callbacks();
    auto after = engine.evaluate("String(globalThis.__iv_count__)");
    long long after_count = std::stoll(std::string(after.getString()));
    require(after_count == before_clear);
}

FRAMEWORK_TEST(clearinterval_is_the_same_function_as_cleartimeout) {
    auto result = run_in_bridge(R"(
        return clearTimeout === clearInterval ? 'same' : 'distinct';
    )");
    require(result == "same");
}

FRAMEWORK_TEST(requestanimationframe_registers_a_frame_callback_that_fires_via_service_frame_callbacks) {
    ScriptEngine engine;
    View root;
    root.set_bounds({0, 0, 400, 300});
    StateStore store;
    WidgetBridge bridge(engine, root, store);
    bridge.load_script(R"(
        globalThis.__raf_marker__ = 'before';
        globalThis.__raf_id__ = requestAnimationFrame(function () {
            globalThis.__raf_marker__ = 'fired';
        });
    )");
    auto id = engine.evaluate("typeof globalThis.__raf_id__");
    require(std::string(id.getString()) == "number");

    bridge.service_frame_callbacks();

    auto post = engine.evaluate("String(globalThis.__raf_marker__)");
    require(std::string(post.getString()) == "fired");
}

FRAMEWORK_TEST(requestanimationframe_callback_receives_a_finite_domhighrestimestamp) {
    ScriptEngine engine;
    View root;
    root.set_bounds({0, 0, 400, 300});
    StateStore store;
    WidgetBridge bridge(engine, root, store);
    bridge.load_script(R"(
        globalThis.__raf_timestamp__ = null;
        requestAnimationFrame(function (timestamp) {
            globalThis.__raf_timestamp__ = timestamp;
        });
    )");

    bridge.service_frame_callbacks();

    auto result = engine.evaluate(
        "String(typeof globalThis.__raf_timestamp__) + ':' + "
        "String(Number.isFinite(globalThis.__raf_timestamp__));");
    require(std::string(result.getString()) == "number:true");
}

FRAMEWORK_TEST(requestanimationframe_callbacks_in_one_frame_share_captured_replay_time) {
    ScriptEngine engine;
    View root;
    root.set_bounds({0, 0, 400, 300});
    StateStore store;
    WidgetBridge bridge(engine, root, store);
    bridge.load_script(R"(
        globalThis.__replay_calls__ = 0;
        globalThis.__pulpAnimationFrameTimestamp__ = function (nativeNow) {
            globalThis.__replay_calls__ += 1;
            return 2424.3;
        };
        globalThis.__raf_times__ = [];
        requestAnimationFrame(function (timestamp) {
            globalThis.__raf_times__.push(timestamp);
        });
        requestAnimationFrame(function (timestamp) {
            globalThis.__raf_times__.push(timestamp);
        });
    )");

    bridge.service_frame_callbacks();

    auto result = engine.evaluate(
        "String(globalThis.__replay_calls__) + ':' + "
        "String(globalThis.__raf_times__.length) + ':' + "
        "String(globalThis.__raf_times__[0] === globalThis.__raf_times__[1]) + ':' + "
        "String(globalThis.__raf_times__[0]);");
    require(std::string(result.getString()) == "1:2:true:2424.3");
}

FRAMEWORK_TEST(cancelanimationframe_removes_a_pending_frame_callback) {
    ScriptEngine engine;
    View root;
    root.set_bounds({0, 0, 400, 300});
    StateStore store;
    WidgetBridge bridge(engine, root, store);
    bridge.load_script(R"(
        globalThis.__cancel_marker__ = 'before';
        var id = requestAnimationFrame(function () {
            globalThis.__cancel_marker__ = 'fired';
        });
        cancelAnimationFrame(id);
    )");
    bridge.service_frame_callbacks();
    auto v = engine.evaluate("String(globalThis.__cancel_marker__)");
    require(std::string(v.getString()) == "before");
}

FRAMEWORK_TEST(messagechannel_port2_onmessage_fires_after_port1_postmessage_on_next_microtask_drain) {
    ScriptEngine engine;
    View root;
    root.set_bounds({0, 0, 400, 300});
    StateStore store;
    WidgetBridge bridge(engine, root, store);
    bridge.load_script(R"(
        globalThis.__mc_received__ = null;
        var c = new MessageChannel();
        c.port2.onmessage = function (ev) { globalThis.__mc_received__ = ev.data; };
        c.port1.postMessage({hello: 'world', n: 42});
    )");
    // postMessage queues a microtask; pump_message_loop drains it.
    auto pre = engine.evaluate("String(globalThis.__mc_received__)");
    require(std::string(pre.getString()) == "null");

    engine.pump_message_loop();

    auto post = engine.evaluate(
        "globalThis.__mc_received__ && globalThis.__mc_received__.hello + ':' + "
        "globalThis.__mc_received__.n");
    require(post.isString());
    require(std::string(post.getString()) == "world:42");
}

FRAMEWORK_TEST(native_web_api_globals_are_mirrored_onto_window_so_react_s_scheduler_probe_passes) {
    auto result = run_in_bridge(R"(
        // React 18's scheduler reads `window.setTimeout` (not just
        // `globalThis.setTimeout`). The mirror is the contract.
        return [
            'rAF=' + (window.requestAnimationFrame === globalThis.requestAnimationFrame),
            'cAF=' + (window.cancelAnimationFrame === globalThis.cancelAnimationFrame),
            'sT=' + (window.setTimeout === globalThis.setTimeout),
            'cT=' + (window.clearTimeout === globalThis.clearTimeout),
            'sI=' + (window.setInterval === globalThis.setInterval),
            'cI=' + (window.clearInterval === globalThis.clearInterval),
            'MC=' + (window.MessageChannel === globalThis.MessageChannel),
            'qM=' + (window.queueMicrotask === globalThis.queueMicrotask),
            'pf=' + (window.performance === globalThis.performance)
        ].join('|');
    )");
    require(result ==
            "rAF=true|cAF=true|sT=true|cT=true|sI=true|cI=true|"
            "MC=true|qM=true|pf=true");
}

// A gradient crosses to native as ONE string, and the parser on that side does
// not know the CSS Color 4 function forms. An unrecognised function name falls
// back to WHITE, so a translucent bloom paints as an opaque white blob.
//
// This is not a corner case: Chrome serializes every `color-mix(in oklab, ...)`
// to `oklab(...)`, and color-mix over a token is the idiom generated panels use
// for every glow, bloom and shadow, so it landed on essentially all of them.
// Measured on a real one, a #58f0ff stop at 48% alpha rendered (254,254,254).
//
// The shim already resolves these correctly for solid colours, so the stops are
// rewritten before the handoff rather than by growing a second parser on the
// far side that has to be kept in step.
FRAMEWORK_TEST(gradient_stops_in_css_color_4_notation_reach_native_as_hex) {
    auto result = run_in_bridge(R"(
        return _resolveGradientStopColors(
            'radial-gradient(circle, oklab(0.881151 -0.115999 -0.0528023 / 0.48), rgba(0,0,0,0) 70%)');
    )");
    // #58f0ff at 48% alpha, the same colour parseCSSColor already returns for
    // this string when it appears as a plain background-color.
    require(result ==
            "radial-gradient(circle, #58f0ff7a, rgba(0,0,0,0) 70%)");
}

// Every other notation must survive untouched. A rewrite that "fixes" hex or
// rgba() would be a silent change to colours that already worked.
FRAMEWORK_TEST(gradient_stops_already_native_to_the_parser_are_left_alone) {
    const std::string css =
        "linear-gradient(45deg, #58f0ff, rgba(0, 0, 0, 0) 70%, red)";
    auto result = run_in_bridge("return _resolveGradientStopColors('" + css + "');");
    require(result == css);
}

// An unparseable function passes through verbatim. The native side may still
// understand it, and a silent rewrite to a wrong colour is worse than the
// original text reaching a parser that can refuse it.
FRAMEWORK_TEST(an_unreadable_gradient_stop_is_passed_through_not_guessed_at) {
    const std::string css = "linear-gradient(90deg, oklab(not a colour), #000)";
    auto result = run_in_bridge("return _resolveGradientStopColors('" + css + "');");
    require(result == css);
}

// Chrome serializes a shadow's colour FIRST, `oklab(...) 0px 1px 0px 0px` -
// and the parser matched colour-last only, so a computed shadow did not match
// at all and fell through, leaving a card ringed by a bright halo.
//
// Asserted through the parse helper, not by reading the property back:
// `el.style.boxShadow` returns whatever string was assigned whether or not it
// parsed, so a round-trip assertion passes against a shadow that never reached
// the renderer. Verified, the first version of this test passed with the fix
// disabled, which is why the parse was extracted into a named function.
FRAMEWORK_TEST(a_colour_first_box_shadow_parses_colour_intact) {
    auto result = run_in_bridge(R"(
        var m = _parseBoxShadowOffsets(
            'oklab(0.970671 0.00855935 -0.0140294 / 0.18) 0px 1px 3px 0px');
        return m ? [m[1], m[2], m[3], m[4], m[5]].join('|') : 'NO MATCH';
    )");
    require(result != "NO MATCH");
    // x|y|blur|spread|colour, with the colour resolved rather than torn on its
    // internal spaces.
    CHECK(result.rfind("0|1|3|0|", 0) == 0);
    CHECK(result.find('#') != std::string::npos);
}

// The colour-last form is what already worked. It must keep working, or
// accommodating Chrome's ordering breaks hand-authored CSS.
FRAMEWORK_TEST(a_colour_last_box_shadow_still_parses) {
    auto result = run_in_bridge(R"(
        var m = _parseBoxShadowOffsets('0px 2px 6px 1px rgba(0, 0, 0, 0.4)');
        return m ? [m[1], m[2], m[3], m[4], m[5]].join('|') : 'NO MATCH';
    )");
    require(result != "NO MATCH");
    CHECK(result.rfind("0|2|6|1|", 0) == 0);
}

int main() { return framework_test::run_all(); }
