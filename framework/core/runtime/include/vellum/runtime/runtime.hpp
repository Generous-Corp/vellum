#pragma once

// Convenience header — includes all runtime utilities
//
// ── Thread synchronization strategy ─────────────────────────────────────
//
// Pulp uses different primitives depending on the data flow pattern:
//
// | Pattern                    | Primitive        | Example                      |
// |----------------------------|------------------|------------------------------|
// | Single value, latest-wins  | std::atomic<T>   | Parameter values, flags      |
// | Multi-field coherent read  | SeqLock<T>       | Transport state (tempo+beat) |
// | Large data swap            | TripleBuffer<T>  | Wavetables, IR buffers       |
// | Ordered event stream       | SPSC FIFO        | MIDI events, UI commands     |
// | Occurrence signals/counts  | ActivityChannel  | Pad flashes, UI triggers     |
// | Latest-value metering      | TripleBuffer<T>  | Audio→UI meter data          |
// | Prepared read-only pointer | RealtimeResourceSlot<T,N> | prepared samples/IRs |
//
// RealtimeResourceSlot is a raw-pointer publication helper, not reader-pinned
// RCU: one non-RT owner serializes publish/reclaim and reclaims only after a
// block-boundary grace point. Use the Slot/Handoff taxonomy where a site needs
// multi-reader pins or audio-thread ownership transfer.
//
// NEVER use on the audio thread:
//   std::mutex, std::condition_variable, heap allocation, I/O
//
// Memory ordering:
//   - ParamValue uses relaxed atomics (independent single values, no ordering
//     dependency between parameters)
//   - SeqLock uses acquire/release (ensures coherent multi-field snapshots)
//   - TripleBuffer uses acquire/release on the flag word
//   - EventLoop uses acquire/release + condition_variable (UI thread only)
//   - RealtimeResourceSlot publishes a prepared pointer with release/acquire;
//     reclaim retired resources away from the audio thread after a grace point

#include <vellum/runtime/assert.hpp>
#include <vellum/runtime/activity_channel.hpp>
#include <vellum/runtime/alive_token.hpp>
#include <vellum/runtime/background_job.hpp>
#include <vellum/runtime/cancel.hpp>
#include <vellum/runtime/log.hpp>
#include <vellum/runtime/fixed_function.hpp>
#include <vellum/runtime/log_sink.hpp>
#include <vellum/runtime/node_abi.hpp>
#include <vellum/runtime/scope_guard.hpp>
#include <vellum/runtime/spsc_queue.hpp>
#include <vellum/runtime/system.hpp>
#include <vellum/runtime/seqlock.hpp>
#include <vellum/runtime/triple_buffer.hpp>
#include <vellum/runtime/external_input.hpp>
