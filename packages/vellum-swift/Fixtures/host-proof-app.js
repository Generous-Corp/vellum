(() => {
  let count = 0;
  let title = "";
  let submitted = "";
  const render = () => JSON.stringify({
    protocol: "vellum.authoring-host.v1",
    tree: {
      type: "stack",
      id: "proof-screen",
      style: { width: 360, height: 240, padding: 20, gap: 14, backgroundColor: "#0F172A" },
      children: [
        { type: "text", id: "proof-heading",
          style: { height: 28, color: "#F8FAFC", fontSize: 20 },
          children: [{ type: "text-run", id: "proof-heading/text",
            text: `Swift host proof ${count}`, children: [] }] },
        { type: "button", id: "proof-increment",
          accessibilityLabel: `Increment, count ${count}`,
          style: { width: 180, height: 44, backgroundColor: "#2563EB", borderRadius: 8,
                   color: "#FFFFFF" },
          events: { press: "proof:increment" },
          children: [{ type: "text-run", id: "proof-increment/text",
            text: "Increment", children: [] }] },
        { type: "text-input", id: "proof-title", primitiveVersion: 1,
          value: title, placeholder: "Title",
          selection: { start: title.length, end: title.length },
          accessibilityLabel: "Proof title",
          style: { width: 280, height: 44, backgroundColor: "#1E293B", color: "#E2E8F0" },
          events: { change: "proof:title", submit: "proof:submit" },
          children: [] },
        { type: "view", id: "proof-swatch",
          style: { width: 320, height: 24, backgroundColor: submitted ? "#22C55E" : "#F59E0B" },
          children: [] }
      ]
    }
  });
  globalThis.__vellum = {
    protocol: "vellum.authoring-host.v1",
    renderJSON: render,
    dispatchJSON(request) {
      const parsed = JSON.parse(request);
      if (parsed.action === "proof:increment") count += 1;
      if (parsed.action === "proof:title") title = String(parsed.payload.value);
      if (parsed.action === "proof:submit") submitted = String(parsed.payload.value);
      return render();
    },
    snapshotStateJSON() {
      return JSON.stringify({ protocol: "vellum.authoring-host.v1",
        state: { count, title, submitted } });
    },
    restoreStateJSON(snapshot) {
      const state = JSON.parse(snapshot).state;
      count = state.count;
      title = state.title;
      submitted = state.submitted;
      return render();
    }
  };
})();
