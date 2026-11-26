// Counter panel for the app-host visual fixtures: pressing "go" widens "bar"
// by 40 units. No text, so a capture does not depend on installed fonts.
(() => {
  let count = 0; let dirty = true;
  const tree = () => ({ type: "stack", id: "screen",
    style: { width: 240, height: 120, backgroundColor: "#0F172A", padding: 10 },
    children: [
      { type: "view", id: "bar", style: { width: 40 + count * 40, height: 40, backgroundColor: "#14B8A6" }, children: [] },
      { type: "button", id: "go", events: { press: "inc" }, style: { width: 80, height: 30, backgroundColor: "#F59E0B" }, children: [] },
      { type: "view", id: "ramp", style: { width: 200, height: 20, backgroundLinearGradient: { angle: 90, stops: [{ position: 0.01, color: "#F8FAFC" }, { position: 1, color: "#EF4444" }] } }, children: [] } ] });
  const render = () => { dirty = false; return JSON.stringify({ protocol: "vellum.authoring-host.v1", tree: tree() }); };
  globalThis.__vellum = { protocol: "vellum.authoring-host.v1", hostProtocol: "vellum.authoring-host.v2",
    renderJSON: render, dispatchJSON(e) { count++; return render(); }, isDirty() { return dirty; },
    pumpJSON() { dirty = false; return JSON.stringify({ protocol: "vellum.authoring-host.v2", kind: "render-result", revision: count, tree: tree() }); } };
})();
