// Streaming — TS fallback for the Kafka→Flink→ClickHouse transport.
//
// This is the in-binary transport the hackathon actually runs. Every
// call to `publishDecisionEvent` is the single seam where, in
// production, the bytes would go to an MSK Kafka topic instead of an
// in-memory ring buffer. The CEP engine below (`detectRapidRejects`)
// is a faithful TS re-implementation of the PyFlink CEP pattern in
// src/stream/flink_job.py — the same predicate (≥3 REJECTs from the
// same customer_id within 5 minutes) so a fraud ring fires the same
// alert in both runtimes.
//
// See docs/STREAMING_ARCHITECTURE.md for the production swap plan and
// the exactly-once-semantics discussion.

import type { Decision } from "@/lib/mock-data";

/** A single decision event flowing through the stream. */
export interface DecisionEvent {
  /** Unique event id (so consumers can dedupe — exactly-once-lite). */
  event_id: string;
  /** Customer this decision concerns. */
  customer_id: string;
  /** The order this decision was made for. */
  order_id: string;
  /** The decision emitted by /risk/score. */
  decision: Decision | null;
  /** The risk probability the model produced. */
  probability: number | null;
  /** Free-form reason the scorer cited (rule_fired, mandate, etc.). */
  reason: string | null;
  /** ISO-8601 timestamp when the event was emitted. */
  timestamp: string;
  /** Counter incremented for every event — useful in tests. */
  seq: number;
}

/**
 * The transport singleton. Wraps an in-memory ring buffer. In
 * production this exact interface is implemented by a Kafka-backed
 * client; the swap is one file, see § Production swap in
 * docs/STREAMING_ARCHITECTURE.md.
 */
export class DecisionStream {
  private buffer: DecisionEvent[] = [];
  private consumers: Array<(ev: DecisionEvent) => void> = [];
  private seq = 0;
  /** Max events the in-memory store retains (ring buffer cap). */
  private readonly cap = 5_000;

  /**
   * Push a decision event onto the stream. Notifies every registered
   * consumer synchronously, then truncates the buffer if over the cap.
   *
   * @param ev - the decision event (without seq/event_id — those are added here)
   * @returns the persisted event (with seq + event_id)
   */
  publishDecisionEvent(
    ev: Omit<DecisionEvent, "event_id" | "seq">,
  ): DecisionEvent {
    const seq = ++this.seq;
    const full: DecisionEvent = {
      ...ev,
      event_id: `evt-${seq}-${Date.now().toString(36)}`,
      seq,
    };
    this.buffer.push(full);
    if (this.buffer.length > this.cap) {
      this.buffer.splice(0, this.buffer.length - this.cap);
    }
    for (const c of this.consumers) {
      try {
        c(full);
      } catch {
        // A bad consumer must not break the publish path.
        continue;
      }
    }
    return full;
  }

  /**
   * Register a consumer that receives every event as it's published.
   * Returns an unsubscribe function.
   */
  consumeDecisionStream(handler: (ev: DecisionEvent) => void): () => void {
    this.consumers.push(handler);
    return () => {
      const i = this.consumers.indexOf(handler);
      if (i >= 0) this.consumers.splice(i, 1);
    };
  }

  /**
   * Drain the last N events from the buffer (for the /events GET
   * endpoint and the dashboard's Recent Decisions panel).
   */
  recent(limit = 50): DecisionEvent[] {
    return this.buffer.slice(-limit).reverse();
  }

  /**
   * CEP pattern — detect a rapid sequence of REJECT decisions from
   * the same customer within the given window.
   *
   * This is the TS mirror of the PyFlink CEP pattern in
   * src/stream/flink_job.py: `Pattern.begin("r1").where(decision ==
   * REJECT).followedBy("r2").where(...).within(Time.minutes(5))`.
   *
   * @param customerId - the customer under inspection
   * @param windowMs - sliding window size (default 5 minutes)
   * @param threshold - minimum REJECT count to fire (default 3)
   * @returns true if the customer has ≥ threshold REJECTs in window
   */
  detectRapidRejects(
    customerId: string,
    windowMs = 300_000,
    threshold = 3,
  ): boolean {
    const now = Date.now();
    const windowStart = now - windowMs;
    let count = 0;
    // Walk the buffer newest→oldest so we can short-circuit on the
    // first event older than the window.
    for (let i = this.buffer.length - 1; i >= 0; i--) {
      const ev = this.buffer[i];
      const ts = Date.parse(ev.timestamp);
      if (Number.isNaN(ts)) continue;
      if (ts < windowStart) break;
      if (ev.customer_id === customerId && ev.decision === "REJECT") {
        count++;
        if (count >= threshold) return true;
      }
    }
    return false;
  }

  /** Total events ever published (monotonic counter). */
  get totalPublished(): number {
    return this.seq;
  }

  /** Clear the buffer (used by tests + the dev console reset button). */
  reset(): void {
    this.buffer = [];
  }
}

/**
 * Singleton instance — the entire app talks to this one stream. In
 * production the class is the same, but the underlying transport
 * (publishDecisionEvent body) calls into confluent-kafka.
 */
export const stream = new DecisionStream();
