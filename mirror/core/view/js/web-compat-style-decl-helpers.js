// ═══════════════════════════════════════════════════════════════════════════════
// CSSStyleDeclaration helpers (P5-5 first cut — extracted from web-compat-style-decl.js)
// ═══════════════════════════════════════════════════════════════════════════════
//
// Three orthogonal pieces that the U-2 export map calls out as the
// natural seam:
//
//   1. `_cssToFlex(v)` — CSS-keyword → Yoga-flex-value translator. Used
//      by the layout property switch inside the big `_applyProperty` body.
//   2. `__cssProperties__` — exhaustive list of camelCase CSS property
//      names that get the getter/setter reflection.
//   3. The IIFE that walks `__cssProperties__` and defines a
//      `Object.defineProperty(CSSStyleDeclaration.prototype, name, ...)`
//      pair for each entry. This is what makes `el.style.flexDirection = "row"`
//      reach `_applyProperty("flexDirection", "row")` at runtime.
//   4. `setProperty` / `getPropertyValue` / `removeProperty` — the W3C
//      CSSStyleDeclaration API surface that the per-property reflection
//      forwards to.
//
// Embed order: this file is loaded AFTER web-compat-style-decl.js so
// the `CSSStyleDeclaration` constructor + `_applyProperty` prototype
// method are already defined when the IIFE here walks the property
// list. `_cssToFlex` is a function declaration so it hoists across
// embed boundaries safely.

function _cssToFlex(v) {
    if (v === "flex-start") return "start";
    if (v === "flex-end") return "end";
    if (v === "space-between") return "space-between";
    if (v === "space-around") return "space-around";
    if (v === "space-evenly") return "space-evenly";
    return v; // center, stretch pass through
}

// Define style property getters/setters via Proxy-like approach
// Since QuickJS supports Proxy, use it for the style object
// But for safety and compatibility, we use defineProperty on the prototype
var __cssProperties__ = [
    "display", "flexDirection", "flexWrap", "flexGrow", "flexShrink", "flexBasis", "flex",
    "flexFlow", "justifyContent", "alignItems", "alignSelf", "alignContent", "order",
    "placeItems", "placeContent",
    "gap", "rowGap", "columnGap",
    "width", "height", "minWidth", "minHeight", "maxWidth", "maxHeight",
    "aspectRatio", "boxSizing",
    "margin", "marginTop", "marginRight", "marginBottom", "marginLeft",
    "marginInline", "marginBlock",
    // pulp #1434 A4 Bundle 3 — CSS logical-edge longhands.
    "marginInlineStart", "marginInlineEnd", "marginBlockStart", "marginBlockEnd",
    "paddingInlineStart", "paddingInlineEnd", "paddingBlockStart", "paddingBlockEnd",
    // pulp #1434 batch 4 — React Native shorthand aliases.
    "marginHorizontal", "marginVertical",
    "padding", "paddingTop", "paddingRight", "paddingBottom", "paddingLeft",
    "paddingInline", "paddingBlock",
    "paddingHorizontal", "paddingVertical",
    "backgroundColor", "color",
    "fontSize", "fontWeight", "fontStyle", "fontFamily", "letterSpacing", "lineHeight",
    "textAlign", "textTransform",
    // pulp #1434 (batch 3) — text-decoration shorthand + 3 longhands.
    "textDecoration", "textDecorationLine", "textDecorationColor", "textDecorationStyle",
    "textOverflow", "textShadow",
    "whiteSpace", "wordBreak", "overflowWrap", "wordWrap",
    "border", "borderColor", "borderWidth", "borderRadius",
    "borderTop", "borderRight", "borderBottom", "borderLeft",
    "borderTopWidth", "borderRightWidth", "borderBottomWidth", "borderLeftWidth",
    "borderTopColor", "borderRightColor", "borderBottomColor", "borderLeftColor",
    "borderTopLeftRadius", "borderTopRightRadius", "borderBottomLeftRadius", "borderBottomRightRadius",
    "outline", "outlineWidth", "outlineColor", "outlineOffset", "outlineStyle",
    "opacity", "overflow", "cursor", "visibility",
    // pulp #1434 A4 Bundle 4 — overflow per-axis (axis-tied gotcha).
    "overflowX", "overflowY",
    "userSelect", "pointerEvents",
    "transform", "transformOrigin",
    "transition", "transitionDuration",
    "animation", "animationName", "animationDuration", "animationTimingFunction",
    "animationDelay", "animationIterationCount", "animationDirection", "animationFillMode",
    // pulp #1434 A4 Bundle 2 — animation-play-state.
    "animationPlayState",
    "position", "top", "right", "bottom", "left", "zIndex", "inset",
    "boxShadow", "filter", "backdropFilter", "background", "backgroundImage",
    "backgroundSize", "backgroundPosition", "backgroundRepeat",
    // pulp #1517 — background sub-props (mostly noop / partial in pulp's
    // layout model; see _applyProperty for the per-prop semantics).
    "backgroundAttachment", "backgroundClip", "backgroundOrigin",
    "gridTemplateColumns", "gridTemplateRows", "gridColumn", "gridRow",
    "lineClamp", "webkitLineClamp",
    // pulp #1434 A4 Bundles 5–7 closure — text rendering tail, isolation,
    // clip/mask cluster, direction, resize, fontVariant.
    "textIndent", "verticalAlign", "writingMode",
    "scrollBehavior", "scrollMargin", "scrollPadding", "scrollSnapType",
    "isolation", "mixBlendMode", "clipPath", "mask", "maskImage", "direction",
    "resize", "fontVariant",
    "perspective", "perspectiveOrigin"
];

(function() {
    for (var i = 0; i < __cssProperties__.length; i++) {
        (function(prop) {
            Object.defineProperty(CSSStyleDeclaration.prototype, prop, {
                get: function() { return this._props[prop] || ""; },
                set: function(v) {
                    this._props[prop] = v;
                    this._applyProperty(prop, v);
                },
                enumerable: true, configurable: true
            });
        })(__cssProperties__[i]);
    }
})();

// setProperty / getPropertyValue for CSS variable support
CSSStyleDeclaration.prototype.setProperty = function(name, value) {
    // --custom-property -> set as theme token
    if (name.indexOf("--") === 0) {
        var tokenName = name.slice(2);
        // pulp #1918 (Codex review) — custom-property storage spans
        // three theme slots: dimensions (motion), colors, strings.
        // When a var() is reassigned from one value-type to another
        // (e.g. `--my-var: "red"` → `--my-var: 12px`) the OLD slot
        // would otherwise retain a stale token, and the React-side
        // resolver checks strings BEFORE motion — so the stale string
        // would shadow the new length. Conservatively clear every
        // non-active slot up front, then write the new value.
        if (typeof setStringToken === 'function') setStringToken(tokenName, "");
        if (typeof setMotionToken === 'function') setMotionToken(tokenName, 0);
        var parsed = resolveCSSLength(value);
        if (parsed) {
            setMotionToken(tokenName, parsed.value);
        } else {
            // Color token? Store as a theme color override
            var color = parseCSSColor(value);
            if (color) {
                // Use applyTokenDiff for color tokens
                applyTokenDiff('{"colors":{"' + tokenName + '":"' + color + '"}}');
            } else if (typeof setStringToken === 'function') {
                // pulp #1899 (gap #3) — string-valued custom property
                // (e.g. `--mono: "JetBrains Mono"`). Store on
                // theme.strings so resolveVar() on the React side and
                // getPropertyValue() below can read it back. Without
                // this, fontFamily-shaped custom properties were
                // silently dropped at setProperty time.
                setStringToken(tokenName, String(value));
            }
        }
    } else {
        // Convert CSS property name to camelCase
        var camel = name.replace(/-([a-z])/g, function(_, c) { return c.toUpperCase(); });
        this[camel] = value;
    }
};

CSSStyleDeclaration.prototype.getPropertyValue = function(name) {
    if (name.indexOf("--") === 0) {
        var tokenName = name.slice(2);
        // pulp #1899 (gap #3) — prefer the string-token map for
        // values that weren't parseable as length / color at set time
        // (e.g. font families). Fall back to the motion-token value
        // otherwise to preserve legacy numeric token reads.
        if (typeof getStringToken === 'function') {
            var s = getStringToken(tokenName);
            if (s) return s;
        }
        return String(getMotionToken(tokenName));
    }
    var camel = name.replace(/-([a-z])/g, function(_, c) { return c.toUpperCase(); });
    return this._props[camel] || "";
};

CSSStyleDeclaration.prototype.removeProperty = function(name) {
    var camel = name.replace(/-([a-z])/g, function(_, c) { return c.toUpperCase(); });
    var old = this._props[camel] || "";
    delete this._props[camel];
    return old;
};
