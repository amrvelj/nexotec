/** Thrown by a host's `onCommitted` when its own step after the save fails
 * (attaching to the offer, creating the pipeline item). The configuration
 * is saved; the overlay stays open and shows this message so the advisor
 * can retry without losing anything. */
export class HostCommitError extends Error {}
