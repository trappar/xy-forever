// Public entry (browser and Node): the call layer, typed errors, the Web Serial transport and the
// generated protocol module. Node's TCP transport: the `./node` entry. Developer commands and the
// raw link: the `./dev` entry. Test-only commands exist in neither (src/v1.ts leaves them out).
export { Client, STATE_FILES, TFW_REASONS, parseSlot, sha256 } from './client.ts';
export { fileReason } from './file-reason.ts';
export { PROTECTED_NAMES, WRITE_ROOTS, inWriteArea } from './write-area.ts';
export { SCREEN, decodeScreen } from './screen.ts';
export { ASK_TEXT, NOT_ALLOWED_TEXT, RETRY_MS } from './trust.ts';
export type { Trust } from './trust.ts';
export { MAX_FOLDERS, depthRefusal, foldersOf, readsFlatMods } from './depth.ts';
export type { CallOptions, ConnectOptions, DeviceInfo, DirEntry, EntryKind, ProductCommand, ReplyOf, RequestOf, Staged, TransferOptions } from './client.ts';
export { XyfError, isXyfError } from './errors.ts';
export { JOB_ERRORS, JOB_PHASES, JobError, LIBRARY_ROOT, MISSING_WORDS, MODS_ROOT, PackageError, TMP_ROOT, applyMods, cancelJob, fileListSource, infoValues,
  jobFolder, jobStatus, libraryBases, makeDefault, listPackages, listPaths, localSet, pushPackages, removePackage } from './mods.ts';
export type { ApplyOptions, ApplyRequest, BaseEntry, HeldGeneration, HeldPackage, JobPhase, JobStatus, LocalFile, LocalSet, PackageResult, PackageSource,
  PushOptions, PushSummary, Removed, BuiltEntry, DefaultResult } from './mods.ts';
export { STORE_FILE, STORE_VERSION, storedName, storeVersion } from './modstore.ts';
export { CONFIG_FLAGS, configGet, configSet } from './config.ts';
export type { InputsRequest, SavedInputs } from './config.ts';
export { INDEX_FORMAT, checkIndex, fetchPackages, loadIndex } from './modindex.ts';
export type { FetchOptions, IndexFile, IndexGeneration, IndexPackage, ModIndex } from './modindex.ts';
export type { ErrorCode } from './errors.ts';
export type { Opener, Transport, TransportSink } from './transport.ts';
export { WebSerialTransport, grantedPorts, requestPort, webSerial, webSerialOpener } from './webserial.ts';
export type { SerialLike, SerialPortLike } from './webserial.ts';
export { STATUS, TYPE, USB_FILTERS, VERSION, crc32, statusName } from './v1.ts';
export type { CommandName, LogEvent, StatusName } from './v1.ts';
export * as protocol from './v1.ts';
