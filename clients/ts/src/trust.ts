// #65 Ask before USB control. With the setting on (hello confirm=1), the first command but hello of a connection
// makes the OP-XY ask "Allow this computer to control your OP-XY?", with no time limit. While it asks every command
// but hello answers `waiting` and nothing is done: the call layer sends it again every RETRY_MS (no limit but the
// caller's signal) and tells Client.onAsk listeners (true, then false once the call moves on). After Don't allow
// every command answers `declined` until the cable is unplugged or the device restarts: an XyfError, not allowed.

/** What an app shows while the device asks. */
export const ASK_TEXT = 'Allow on your OP-XY';
/** The `declined` error's text. */
export const NOT_ALLOWED_TEXT = "not allowed: Don't allow was chosen on the OP-XY; unplug the USB cable and plug it in again to be asked again";
export const RETRY_MS = 500;
/** hello trust=: the question on this connection. */
export type Trust = 'none' | 'asking' | 'allowed' | 'refused';
