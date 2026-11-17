#include <vellum/foundation/frame_pump.hpp>

#include <cmath>
#include <cstdio>
#include <string>

namespace {

using namespace vellum::foundation;

int failures = 0;

void check(bool ok, const std::string& what) {
    std::printf("%s %s\n", ok ? "PASS" : "FAIL", what.c_str());
    if (!ok) ++failures;
}

bool near(double value, double expected) { return std::fabs(value - expected) <= 1.0e-9; }

// A stand-in for content that is not a scene: a document that updates itself
// and then draws, animating for `length` seconds of the pump clock.
struct Document final {
    double length = 0.5;
    double drawn_at = -1.0;
    int updates = 0;
    int draws = 0;

    FramePump::AdvanceFunction advance() {
        return [this](FrameTick& tick) {
            ++updates;
            if (tick.reduced_motion && tick.clock < length) {
                tick.dt += length - tick.clock;
                tick.clock = length;
            }
            const bool running = tick.clock < length;
            return FrameStep{.changed = running || drawn_at < length, .wants_more = running};
        };
    }
    FramePump::RenderFunction render(const FramePump& pump) {
        return [this, &pump](std::string*) {
            ++draws;
            drawn_at = pump.clock();
            return true;
        };
    }
};

void animates_then_idles() {
    FramePump pump;
    Document doc;
    double now = 100.0;
    int ticks = 0;
    PumpReport report;
    do {
        report = pump.frame(now, doc.advance(), doc.render(pump));
        now += 0.1;
    } while (report.needs_next_frame && ++ticks < 50);
    check(doc.draws >= 5 && doc.drawn_at >= doc.length && !report.needs_next_frame,
          "a document animates until its end and the pump then asks for nothing");
    const int draws = doc.draws;
    for (int i = 0; i < 5; ++i) {
        report = pump.frame(now, doc.advance(), doc.render(pump));
        now += 0.1;
    }
    check(doc.draws == draws && !report.needs_next_frame, "an idle document draws nothing");
}

void request_and_wake() {
    FramePump pump;
    Document doc;
    int wakes = 0;
    pump.set_wake([&wakes] { ++wakes; });
    double now = 1.0;
    for (int i = 0; i < 10; ++i) {
        pump.frame(now, doc.advance(), doc.render(pump));
        now += 0.1;
    }
    const int draws = doc.draws;
    pump.request_frame();
    pump.request_frame();
    check(wakes == 1, "requests before the frame runs wake once");
    pump.frame(now, doc.advance(), doc.render(pump));
    check(doc.draws == draws + 1, "a request draws one frame");
}

void reduced_motion_reaches_the_content() {
    FramePump pump({.reduced_motion = true});
    Document doc;
    const auto report = pump.frame(1.0, doc.advance(), doc.render(pump));
    check(near(report.clock, doc.length) && !report.needs_next_frame && doc.draws == 1,
          "reduced motion lets the content jump to its end and settle at once");
}

void idle_gap_is_a_resume() {
    FramePump pump;
    Document doc;
    pump.frame(1.0, doc.advance(), doc.render(pump));
    auto report = pump.frame(1.1, doc.advance(), doc.render(pump));
    check(!report.resumed && near(report.dt, 0.1), "a short gap is measured raw");
    report = pump.frame(500.0, doc.advance(), doc.render(pump));
    check(report.resumed && near(report.dt, 1.0 / 60.0), "a long gap is one nominal frame");
    pump.suspend();
    report = pump.frame(500.01, doc.advance(), doc.render(pump));
    check(report.resumed, "a frame after suspend is a resume");
    report = pump.frame(500.0, doc.advance(), doc.render(pump));
    check(near(report.dt, 0.0), "a backwards timestamp advances nothing");
}

void failing_render_is_bounded() {
    FramePump pump;
    int calls = 0;
    const auto advance = [](FrameTick&) { return FrameStep{}; };
    const auto render = [&calls](std::string* error) {
        ++calls;
        if (error != nullptr) *error = "device lost";
        return false;
    };
    double now = 1.0;
    int asked = 0;
    for (int i = 0; i < 20; ++i) {
        const auto report = pump.frame(now, advance, render);
        now += 0.016;
        if (!report.needs_next_frame) break;
        ++asked;
    }
    check(asked == 3 && calls == 4, "a failing render is retried a bounded number of times");
}

}  // namespace

int main() {
    animates_then_idles();
    request_and_wake();
    reduced_motion_reaches_the_content();
    idle_gap_is_a_resume();
    failing_render_is_bounded();
    std::printf("%d failure(s)\n", failures);
    return failures == 0 ? 0 : 1;
}
