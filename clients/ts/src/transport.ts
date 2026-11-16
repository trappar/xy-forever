// A byte link to the device: the USB CDC port (Web Serial) or the simulator's TCP port (Node).
// Both carry exactly the protocol's frame stream; nothing here knows about frames.

export interface TransportSink {
  /** Bytes as they arrive, in any split. */
  data(bytes: Uint8Array): void;
  /** The link ended (the peer closed it, the cable went, or close() was called); called once. */
  closed(error?: Error): void;
}

export interface Transport {
  /** Starts delivering received bytes to sink. Called once, right after the transport opens. */
  start(sink: TransportSink): void;
  /** Sends bytes; rejects once the link is gone. */
  write(data: Uint8Array): Promise<void>;
  /** Ends the link (sink.closed follows). Safe to call more than once. */
  close(): Promise<void>;
}

/** Opens a new transport to the device: called on connect and again after a restart. */
export type Opener = (signal?: AbortSignal) => Promise<Transport>;
