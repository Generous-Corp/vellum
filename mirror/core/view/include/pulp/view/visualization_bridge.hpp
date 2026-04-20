#pragma once

/// @file visualization_bridge.hpp
/// Lock-free bridge for audio visualization data: STFT spectra, multi-channel
/// metering, and waveform capture. Manages all audio→UI publication paths
/// for visualization through a single configuration point.
///
/// Audio thread calls process(). UI thread reads latest data.
/// All transport uses TripleBuffer (latest-value, no blocking, no allocation).
///
/// Forward-compatible with Phase 13 Three.js bridge: the same data published
/// here is accessible from JS via AudioBridge → widget_bridge.cpp bindings.

#include <pulp/runtime/triple_buffer.hpp>
#include <pulp/signal/stft.hpp>
#include <pulp/signal/multi_channel_meter.hpp>
#include <pulp/signal/spectrogram.hpp>
#include <array>
#include <cmath>
#include <algorithm>
#include <vector>

#ifdef PULP_BENCHMARK
#include <pulp/render/bench/perf_counters.hpp>
#endif

namespace pulp::view {

/// Published spectrum data (lock-free via TripleBuffer).
struct SpectrumData {
    static constexpr int kMaxBins = 4097; // FFT 8192 → 4097 bins

    std::array<float, kMaxBins> magnitude_db{};
    int num_bins = 0;
};

/// Published waveform capture (latest buffer snapshot).
struct WaveformData {
    static constexpr int kMaxSamples = 8192;

    std::array<float, kMaxSamples> samples{};
    int num_samples = 0;
    int num_channels = 0; // 1 = mono (mixed), 2+ = first channel only for now
};

/// Configuration for the visualization bridge.
struct VisualizationConfig {
    // STFT
    int fft_size = 1024;
    int hop_size = 256;
    signal::WindowFunction::Type window = signal::WindowFunction::Type::hann;
    float window_param = 0.0f;

    // Metering
    int num_channels = 2;
    float sample_rate = 44100.0f;

    // Waveform capture
    bool capture_waveform = true;
    int waveform_length = 1024; // samples to capture per snapshot
};

/// Central visualization bridge: single entry point for all audio→UI
/// visualization data. Wraps STFT, multi-channel metering, and waveform
/// capture with lock-free TripleBuffer publication.
///
/// Thread model:
///   - Audio thread: calls process() from the audio callback
///   - UI thread: calls read_spectrum(), read_meter(), read_waveform()
///
/// The bridge owns the STFT processor and multi-channel meter internally.
/// No external STFT or meter instances needed.
class VisualizationBridge {
public:
    VisualizationBridge() = default;

    /// Configure all visualization subsystems. Call before first process().
    void configure(const VisualizationConfig& config) {
        config_ = config;

        // Configure STFT
        signal::StftConfig stft_cfg;
        stft_cfg.fft_size = config.fft_size;
        stft_cfg.hop_size = config.hop_size;
        stft_cfg.window = config.window;
        stft_cfg.window_param = config.window_param;
        stft_.configure(stft_cfg);

        // Configure meter
        meter_.prepare(config.sample_rate, config.num_channels);

        // Waveform capture setup
        waveform_length_ = std::min(config.waveform_length,
                                     static_cast<int>(WaveformData::kMaxSamples));
        waveform_pos_ = 0;
    }

    /// Process audio from the audio callback. Computes STFT, metering,
    /// and waveform capture, then publishes results lock-free.
    ///
    /// @param channels  Array of channel buffer pointers.
    /// @param num_channels  Number of channels.
    /// @param num_samples  Samples per channel in this block.
    void process(const float* const* channels, int num_channels, int num_samples) {
        // Multi-channel metering
        meter_.process(channels, num_channels, num_samples);
#ifdef PULP_BENCHMARK
        {
            const double t0 = render::bench::now_us();
            meter_buf_.write(meter_.snapshot());
            if (bench_counters_) {
                bench_counters_->triplebuffer_publish_total_us.fetch_add(
                    render::bench::now_us() - t0, std::memory_order_relaxed);
            }
        }
#else
        meter_buf_.write(meter_.snapshot());
#endif

        // STFT on first channel (or mono mix for multi-channel)
        if (num_channels > 0 && num_samples > 0) {
            bool new_frame = stft_.push_samples(channels[0], num_samples);

            if (new_frame) {
                // Publish spectrum
                SpectrumData spec;
                auto db = stft_.latest_magnitude_db(-120.0f);
                spec.num_bins = std::min(static_cast<int>(db.size()),
                                         static_cast<int>(SpectrumData::kMaxBins));
#ifdef PULP_BENCHMARK
                {
                    const double t_copy = render::bench::now_us();
                    std::copy_n(db.data(), spec.num_bins, spec.magnitude_db.data());
                    if (bench_counters_) {
                        bench_counters_->audio_copy_total_us.fetch_add(
                            render::bench::now_us() - t_copy,
                            std::memory_order_relaxed);
                    }
                }
                {
                    const double t_pub = render::bench::now_us();
                    spectrum_buf_.write(spec);
                    if (bench_counters_) {
                        bench_counters_->triplebuffer_publish_total_us.fetch_add(
                            render::bench::now_us() - t_pub,
                            std::memory_order_relaxed);
                    }
                }
#else
                std::copy_n(db.data(), spec.num_bins, spec.magnitude_db.data());
                spectrum_buf_.write(spec);
#endif
            }
        }

        // Waveform capture (ring buffer of latest samples from channel 0)
        if (config_.capture_waveform && num_channels > 0) {
            for (int i = 0; i < num_samples; ++i) {
                waveform_ring_[waveform_pos_] = channels[0][i];
                waveform_pos_ = (waveform_pos_ + 1) % waveform_length_;
            }

            WaveformData wd;
            wd.num_samples = waveform_length_;
            wd.num_channels = num_channels;
#ifdef PULP_BENCHMARK
            {
                const double t_copy = render::bench::now_us();
                // Copy in order from oldest to newest
                for (int i = 0; i < waveform_length_; ++i) {
                    wd.samples[i] = waveform_ring_[(waveform_pos_ + i) % waveform_length_];
                }
                if (bench_counters_) {
                    bench_counters_->audio_copy_total_us.fetch_add(
                        render::bench::now_us() - t_copy,
                        std::memory_order_relaxed);
                }
            }
            {
                const double t_pub = render::bench::now_us();
                waveform_buf_.write(wd);
                if (bench_counters_) {
                    bench_counters_->triplebuffer_publish_total_us.fetch_add(
                        render::bench::now_us() - t_pub,
                        std::memory_order_relaxed);
                }
            }
#else
            // Copy in order from oldest to newest
            for (int i = 0; i < waveform_length_; ++i) {
                wd.samples[i] = waveform_ring_[(waveform_pos_ + i) % waveform_length_];
            }
            waveform_buf_.write(wd);
#endif
        }
    }

#ifdef PULP_BENCHMARK
    /// Install (or clear) the benchmark perf-counter sink. Call from the
    /// UI/main thread before the audio callback starts accumulating.
    /// The pointer is stored by raw reference — caller owns the counters
    /// and must outlive this bridge.
    void set_bench_counters(render::bench::PerfCounters* counters) {
        bench_counters_ = counters;
    }
#endif

    // ── UI thread reads ─────────────────────────────────────────────────────

    /// Read the latest spectrum data (dB magnitudes).
    const SpectrumData& read_spectrum() { return spectrum_buf_.read(); }

    /// Read the latest multi-channel meter data.
    const signal::MultiChannelMeterData& read_meter() { return meter_buf_.read(); }

    /// Read the latest waveform capture.
    const WaveformData& read_waveform() { return waveform_buf_.read(); }

    // ── Accessors ───────────────────────────────────────────────────────────

    int fft_size() const { return config_.fft_size; }
    int num_bins() const { return stft_.num_bins(); }
    int num_channels() const { return config_.num_channels; }
    float sample_rate() const { return config_.sample_rate; }

    /// Reset all internal state (call when playback stops/restarts).
    void reset() {
        stft_.reset();
        meter_.reset();
        waveform_pos_ = 0;
        std::fill(waveform_ring_.begin(), waveform_ring_.end(), 0.0f);
    }

private:
    VisualizationConfig config_;

    // STFT processor (audio thread)
    signal::Stft stft_;

    // Multi-channel meter (audio thread)
    signal::MultiChannelMeter meter_;

    // Lock-free publication buffers
    runtime::TripleBuffer<SpectrumData> spectrum_buf_;
    runtime::TripleBuffer<signal::MultiChannelMeterData> meter_buf_;
    runtime::TripleBuffer<WaveformData> waveform_buf_;

    // Waveform capture ring buffer
    std::array<float, WaveformData::kMaxSamples> waveform_ring_{};
    int waveform_length_ = 1024;
    int waveform_pos_ = 0;

#ifdef PULP_BENCHMARK
    render::bench::PerfCounters* bench_counters_ = nullptr;
#endif
};

} // namespace pulp::view
