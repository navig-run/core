var __defProp = Object.defineProperty;
var __name = (target, value) => __defProp(target, "name", { value, configurable: true });

// node_modules/unenv/dist/runtime/_internal/utils.mjs
// @__NO_SIDE_EFFECTS__
function createNotImplementedError(name) {
  return new Error(`[unenv] ${name} is not implemented yet!`);
}
__name(createNotImplementedError, "createNotImplementedError");
// @__NO_SIDE_EFFECTS__
function notImplemented(name) {
  const fn = /* @__PURE__ */ __name(() => {
    throw /* @__PURE__ */ createNotImplementedError(name);
  }, "fn");
  return Object.assign(fn, { __unenv__: true });
}
__name(notImplemented, "notImplemented");

// node_modules/unenv/dist/runtime/node/internal/perf_hooks/performance.mjs
var _timeOrigin = globalThis.performance?.timeOrigin ?? Date.now();
var _performanceNow = globalThis.performance?.now ? globalThis.performance.now.bind(globalThis.performance) : () => Date.now() - _timeOrigin;
var nodeTiming = {
  name: "node",
  entryType: "node",
  startTime: 0,
  duration: 0,
  nodeStart: 0,
  v8Start: 0,
  bootstrapComplete: 0,
  environment: 0,
  loopStart: 0,
  loopExit: 0,
  idleTime: 0,
  uvMetricsInfo: {
    loopCount: 0,
    events: 0,
    eventsWaiting: 0
  },
  detail: void 0,
  toJSON() {
    return this;
  }
};
var PerformanceEntry = class {
  static {
    __name(this, "PerformanceEntry");
  }
  __unenv__ = true;
  detail;
  entryType = "event";
  name;
  startTime;
  constructor(name, options) {
    this.name = name;
    this.startTime = options?.startTime || _performanceNow();
    this.detail = options?.detail;
  }
  get duration() {
    return _performanceNow() - this.startTime;
  }
  toJSON() {
    return {
      name: this.name,
      entryType: this.entryType,
      startTime: this.startTime,
      duration: this.duration,
      detail: this.detail
    };
  }
};
var PerformanceMark = class PerformanceMark2 extends PerformanceEntry {
  static {
    __name(this, "PerformanceMark");
  }
  entryType = "mark";
  constructor() {
    super(...arguments);
  }
  get duration() {
    return 0;
  }
};
var PerformanceMeasure = class extends PerformanceEntry {
  static {
    __name(this, "PerformanceMeasure");
  }
  entryType = "measure";
};
var PerformanceResourceTiming = class extends PerformanceEntry {
  static {
    __name(this, "PerformanceResourceTiming");
  }
  entryType = "resource";
  serverTiming = [];
  connectEnd = 0;
  connectStart = 0;
  decodedBodySize = 0;
  domainLookupEnd = 0;
  domainLookupStart = 0;
  encodedBodySize = 0;
  fetchStart = 0;
  initiatorType = "";
  name = "";
  nextHopProtocol = "";
  redirectEnd = 0;
  redirectStart = 0;
  requestStart = 0;
  responseEnd = 0;
  responseStart = 0;
  secureConnectionStart = 0;
  startTime = 0;
  transferSize = 0;
  workerStart = 0;
  responseStatus = 0;
};
var PerformanceObserverEntryList = class {
  static {
    __name(this, "PerformanceObserverEntryList");
  }
  __unenv__ = true;
  getEntries() {
    return [];
  }
  getEntriesByName(_name, _type) {
    return [];
  }
  getEntriesByType(type) {
    return [];
  }
};
var Performance = class {
  static {
    __name(this, "Performance");
  }
  __unenv__ = true;
  timeOrigin = _timeOrigin;
  eventCounts = /* @__PURE__ */ new Map();
  _entries = [];
  _resourceTimingBufferSize = 0;
  navigation = void 0;
  timing = void 0;
  timerify(_fn, _options) {
    throw createNotImplementedError("Performance.timerify");
  }
  get nodeTiming() {
    return nodeTiming;
  }
  eventLoopUtilization() {
    return {};
  }
  markResourceTiming() {
    return new PerformanceResourceTiming("");
  }
  onresourcetimingbufferfull = null;
  now() {
    if (this.timeOrigin === _timeOrigin) {
      return _performanceNow();
    }
    return Date.now() - this.timeOrigin;
  }
  clearMarks(markName) {
    this._entries = markName ? this._entries.filter((e) => e.name !== markName) : this._entries.filter((e) => e.entryType !== "mark");
  }
  clearMeasures(measureName) {
    this._entries = measureName ? this._entries.filter((e) => e.name !== measureName) : this._entries.filter((e) => e.entryType !== "measure");
  }
  clearResourceTimings() {
    this._entries = this._entries.filter((e) => e.entryType !== "resource" || e.entryType !== "navigation");
  }
  getEntries() {
    return this._entries;
  }
  getEntriesByName(name, type) {
    return this._entries.filter((e) => e.name === name && (!type || e.entryType === type));
  }
  getEntriesByType(type) {
    return this._entries.filter((e) => e.entryType === type);
  }
  mark(name, options) {
    const entry = new PerformanceMark(name, options);
    this._entries.push(entry);
    return entry;
  }
  measure(measureName, startOrMeasureOptions, endMark) {
    let start;
    let end;
    if (typeof startOrMeasureOptions === "string") {
      start = this.getEntriesByName(startOrMeasureOptions, "mark")[0]?.startTime;
      end = this.getEntriesByName(endMark, "mark")[0]?.startTime;
    } else {
      start = Number.parseFloat(startOrMeasureOptions?.start) || this.now();
      end = Number.parseFloat(startOrMeasureOptions?.end) || this.now();
    }
    const entry = new PerformanceMeasure(measureName, {
      startTime: start,
      detail: {
        start,
        end
      }
    });
    this._entries.push(entry);
    return entry;
  }
  setResourceTimingBufferSize(maxSize) {
    this._resourceTimingBufferSize = maxSize;
  }
  addEventListener(type, listener, options) {
    throw createNotImplementedError("Performance.addEventListener");
  }
  removeEventListener(type, listener, options) {
    throw createNotImplementedError("Performance.removeEventListener");
  }
  dispatchEvent(event) {
    throw createNotImplementedError("Performance.dispatchEvent");
  }
  toJSON() {
    return this;
  }
};
var PerformanceObserver = class {
  static {
    __name(this, "PerformanceObserver");
  }
  __unenv__ = true;
  static supportedEntryTypes = [];
  _callback = null;
  constructor(callback) {
    this._callback = callback;
  }
  takeRecords() {
    return [];
  }
  disconnect() {
    throw createNotImplementedError("PerformanceObserver.disconnect");
  }
  observe(options) {
    throw createNotImplementedError("PerformanceObserver.observe");
  }
  bind(fn) {
    return fn;
  }
  runInAsyncScope(fn, thisArg, ...args) {
    return fn.call(thisArg, ...args);
  }
  asyncId() {
    return 0;
  }
  triggerAsyncId() {
    return 0;
  }
  emitDestroy() {
    return this;
  }
};
var performance = globalThis.performance && "addEventListener" in globalThis.performance ? globalThis.performance : new Performance();

// node_modules/@cloudflare/unenv-preset/dist/runtime/polyfill/performance.mjs
if (!("__unenv__" in performance)) {
  const proto = Performance.prototype;
  for (const key of Object.getOwnPropertyNames(proto)) {
    if (key !== "constructor" && !(key in performance)) {
      const desc = Object.getOwnPropertyDescriptor(proto, key);
      if (desc) {
        Object.defineProperty(performance, key, desc);
      }
    }
  }
}
globalThis.performance = performance;
globalThis.Performance = Performance;
globalThis.PerformanceEntry = PerformanceEntry;
globalThis.PerformanceMark = PerformanceMark;
globalThis.PerformanceMeasure = PerformanceMeasure;
globalThis.PerformanceObserver = PerformanceObserver;
globalThis.PerformanceObserverEntryList = PerformanceObserverEntryList;
globalThis.PerformanceResourceTiming = PerformanceResourceTiming;

// node_modules/unenv/dist/runtime/node/internal/process/hrtime.mjs
var hrtime = /* @__PURE__ */ Object.assign(/* @__PURE__ */ __name(function hrtime2(startTime) {
  const now = Date.now();
  const seconds = Math.trunc(now / 1e3);
  const nanos = now % 1e3 * 1e6;
  if (startTime) {
    let diffSeconds = seconds - startTime[0];
    let diffNanos = nanos - startTime[0];
    if (diffNanos < 0) {
      diffSeconds = diffSeconds - 1;
      diffNanos = 1e9 + diffNanos;
    }
    return [diffSeconds, diffNanos];
  }
  return [seconds, nanos];
}, "hrtime"), { bigint: /* @__PURE__ */ __name(function bigint() {
  return BigInt(Date.now() * 1e6);
}, "bigint") });

// node_modules/unenv/dist/runtime/node/internal/process/process.mjs
import { EventEmitter } from "node:events";

// node_modules/unenv/dist/runtime/node/internal/tty/read-stream.mjs
var ReadStream = class {
  static {
    __name(this, "ReadStream");
  }
  fd;
  isRaw = false;
  isTTY = false;
  constructor(fd) {
    this.fd = fd;
  }
  setRawMode(mode) {
    this.isRaw = mode;
    return this;
  }
};

// node_modules/unenv/dist/runtime/node/internal/tty/write-stream.mjs
var WriteStream = class {
  static {
    __name(this, "WriteStream");
  }
  fd;
  columns = 80;
  rows = 24;
  isTTY = false;
  constructor(fd) {
    this.fd = fd;
  }
  clearLine(dir, callback) {
    callback && callback();
    return false;
  }
  clearScreenDown(callback) {
    callback && callback();
    return false;
  }
  cursorTo(x, y, callback) {
    callback && typeof callback === "function" && callback();
    return false;
  }
  moveCursor(dx, dy, callback) {
    callback && callback();
    return false;
  }
  getColorDepth(env2) {
    return 1;
  }
  hasColors(count, env2) {
    return false;
  }
  getWindowSize() {
    return [this.columns, this.rows];
  }
  write(str, encoding, cb) {
    if (str instanceof Uint8Array) {
      str = new TextDecoder().decode(str);
    }
    try {
      console.log(str);
    } catch {
    }
    cb && typeof cb === "function" && cb();
    return false;
  }
};

// node_modules/unenv/dist/runtime/node/internal/process/node-version.mjs
var NODE_VERSION = "22.14.0";

// node_modules/unenv/dist/runtime/node/internal/process/process.mjs
var Process = class _Process extends EventEmitter {
  static {
    __name(this, "Process");
  }
  env;
  hrtime;
  nextTick;
  constructor(impl) {
    super();
    this.env = impl.env;
    this.hrtime = impl.hrtime;
    this.nextTick = impl.nextTick;
    for (const prop of [...Object.getOwnPropertyNames(_Process.prototype), ...Object.getOwnPropertyNames(EventEmitter.prototype)]) {
      const value = this[prop];
      if (typeof value === "function") {
        this[prop] = value.bind(this);
      }
    }
  }
  // --- event emitter ---
  emitWarning(warning, type, code) {
    console.warn(`${code ? `[${code}] ` : ""}${type ? `${type}: ` : ""}${warning}`);
  }
  emit(...args) {
    return super.emit(...args);
  }
  listeners(eventName) {
    return super.listeners(eventName);
  }
  // --- stdio (lazy initializers) ---
  #stdin;
  #stdout;
  #stderr;
  get stdin() {
    return this.#stdin ??= new ReadStream(0);
  }
  get stdout() {
    return this.#stdout ??= new WriteStream(1);
  }
  get stderr() {
    return this.#stderr ??= new WriteStream(2);
  }
  // --- cwd ---
  #cwd = "/";
  chdir(cwd2) {
    this.#cwd = cwd2;
  }
  cwd() {
    return this.#cwd;
  }
  // --- dummy props and getters ---
  arch = "";
  platform = "";
  argv = [];
  argv0 = "";
  execArgv = [];
  execPath = "";
  title = "";
  pid = 200;
  ppid = 100;
  get version() {
    return `v${NODE_VERSION}`;
  }
  get versions() {
    return { node: NODE_VERSION };
  }
  get allowedNodeEnvironmentFlags() {
    return /* @__PURE__ */ new Set();
  }
  get sourceMapsEnabled() {
    return false;
  }
  get debugPort() {
    return 0;
  }
  get throwDeprecation() {
    return false;
  }
  get traceDeprecation() {
    return false;
  }
  get features() {
    return {};
  }
  get release() {
    return {};
  }
  get connected() {
    return false;
  }
  get config() {
    return {};
  }
  get moduleLoadList() {
    return [];
  }
  constrainedMemory() {
    return 0;
  }
  availableMemory() {
    return 0;
  }
  uptime() {
    return 0;
  }
  resourceUsage() {
    return {};
  }
  // --- noop methods ---
  ref() {
  }
  unref() {
  }
  // --- unimplemented methods ---
  umask() {
    throw createNotImplementedError("process.umask");
  }
  getBuiltinModule() {
    return void 0;
  }
  getActiveResourcesInfo() {
    throw createNotImplementedError("process.getActiveResourcesInfo");
  }
  exit() {
    throw createNotImplementedError("process.exit");
  }
  reallyExit() {
    throw createNotImplementedError("process.reallyExit");
  }
  kill() {
    throw createNotImplementedError("process.kill");
  }
  abort() {
    throw createNotImplementedError("process.abort");
  }
  dlopen() {
    throw createNotImplementedError("process.dlopen");
  }
  setSourceMapsEnabled() {
    throw createNotImplementedError("process.setSourceMapsEnabled");
  }
  loadEnvFile() {
    throw createNotImplementedError("process.loadEnvFile");
  }
  disconnect() {
    throw createNotImplementedError("process.disconnect");
  }
  cpuUsage() {
    throw createNotImplementedError("process.cpuUsage");
  }
  setUncaughtExceptionCaptureCallback() {
    throw createNotImplementedError("process.setUncaughtExceptionCaptureCallback");
  }
  hasUncaughtExceptionCaptureCallback() {
    throw createNotImplementedError("process.hasUncaughtExceptionCaptureCallback");
  }
  initgroups() {
    throw createNotImplementedError("process.initgroups");
  }
  openStdin() {
    throw createNotImplementedError("process.openStdin");
  }
  assert() {
    throw createNotImplementedError("process.assert");
  }
  binding() {
    throw createNotImplementedError("process.binding");
  }
  // --- attached interfaces ---
  permission = { has: /* @__PURE__ */ notImplemented("process.permission.has") };
  report = {
    directory: "",
    filename: "",
    signal: "SIGUSR2",
    compact: false,
    reportOnFatalError: false,
    reportOnSignal: false,
    reportOnUncaughtException: false,
    getReport: /* @__PURE__ */ notImplemented("process.report.getReport"),
    writeReport: /* @__PURE__ */ notImplemented("process.report.writeReport")
  };
  finalization = {
    register: /* @__PURE__ */ notImplemented("process.finalization.register"),
    unregister: /* @__PURE__ */ notImplemented("process.finalization.unregister"),
    registerBeforeExit: /* @__PURE__ */ notImplemented("process.finalization.registerBeforeExit")
  };
  memoryUsage = Object.assign(() => ({
    arrayBuffers: 0,
    rss: 0,
    external: 0,
    heapTotal: 0,
    heapUsed: 0
  }), { rss: /* @__PURE__ */ __name(() => 0, "rss") });
  // --- undefined props ---
  mainModule = void 0;
  domain = void 0;
  // optional
  send = void 0;
  exitCode = void 0;
  channel = void 0;
  getegid = void 0;
  geteuid = void 0;
  getgid = void 0;
  getgroups = void 0;
  getuid = void 0;
  setegid = void 0;
  seteuid = void 0;
  setgid = void 0;
  setgroups = void 0;
  setuid = void 0;
  // internals
  _events = void 0;
  _eventsCount = void 0;
  _exiting = void 0;
  _maxListeners = void 0;
  _debugEnd = void 0;
  _debugProcess = void 0;
  _fatalException = void 0;
  _getActiveHandles = void 0;
  _getActiveRequests = void 0;
  _kill = void 0;
  _preload_modules = void 0;
  _rawDebug = void 0;
  _startProfilerIdleNotifier = void 0;
  _stopProfilerIdleNotifier = void 0;
  _tickCallback = void 0;
  _disconnect = void 0;
  _handleQueue = void 0;
  _pendingMessage = void 0;
  _channel = void 0;
  _send = void 0;
  _linkedBinding = void 0;
};

// node_modules/@cloudflare/unenv-preset/dist/runtime/node/process.mjs
var globalProcess = globalThis["process"];
var getBuiltinModule = globalProcess.getBuiltinModule;
var workerdProcess = getBuiltinModule("node:process");
var unenvProcess = new Process({
  env: globalProcess.env,
  hrtime,
  // `nextTick` is available from workerd process v1
  nextTick: workerdProcess.nextTick
});
var { exit, features, platform } = workerdProcess;
var {
  _channel,
  _debugEnd,
  _debugProcess,
  _disconnect,
  _events,
  _eventsCount,
  _exiting,
  _fatalException,
  _getActiveHandles,
  _getActiveRequests,
  _handleQueue,
  _kill,
  _linkedBinding,
  _maxListeners,
  _pendingMessage,
  _preload_modules,
  _rawDebug,
  _send,
  _startProfilerIdleNotifier,
  _stopProfilerIdleNotifier,
  _tickCallback,
  abort,
  addListener,
  allowedNodeEnvironmentFlags,
  arch,
  argv,
  argv0,
  assert,
  availableMemory,
  binding,
  channel,
  chdir,
  config,
  connected,
  constrainedMemory,
  cpuUsage,
  cwd,
  debugPort,
  disconnect,
  dlopen,
  domain,
  emit,
  emitWarning,
  env,
  eventNames,
  execArgv,
  execPath,
  exitCode,
  finalization,
  getActiveResourcesInfo,
  getegid,
  geteuid,
  getgid,
  getgroups,
  getMaxListeners,
  getuid,
  hasUncaughtExceptionCaptureCallback,
  hrtime: hrtime3,
  initgroups,
  kill,
  listenerCount,
  listeners,
  loadEnvFile,
  mainModule,
  memoryUsage,
  moduleLoadList,
  nextTick,
  off,
  on,
  once,
  openStdin,
  permission,
  pid,
  ppid,
  prependListener,
  prependOnceListener,
  rawListeners,
  reallyExit,
  ref,
  release,
  removeAllListeners,
  removeListener,
  report,
  resourceUsage,
  send,
  setegid,
  seteuid,
  setgid,
  setgroups,
  setMaxListeners,
  setSourceMapsEnabled,
  setuid,
  setUncaughtExceptionCaptureCallback,
  sourceMapsEnabled,
  stderr,
  stdin,
  stdout,
  throwDeprecation,
  title,
  traceDeprecation,
  umask,
  unref,
  uptime,
  version,
  versions
} = unenvProcess;
var _process = {
  abort,
  addListener,
  allowedNodeEnvironmentFlags,
  hasUncaughtExceptionCaptureCallback,
  setUncaughtExceptionCaptureCallback,
  loadEnvFile,
  sourceMapsEnabled,
  arch,
  argv,
  argv0,
  chdir,
  config,
  connected,
  constrainedMemory,
  availableMemory,
  cpuUsage,
  cwd,
  debugPort,
  dlopen,
  disconnect,
  emit,
  emitWarning,
  env,
  eventNames,
  execArgv,
  execPath,
  exit,
  finalization,
  features,
  getBuiltinModule,
  getActiveResourcesInfo,
  getMaxListeners,
  hrtime: hrtime3,
  kill,
  listeners,
  listenerCount,
  memoryUsage,
  nextTick,
  on,
  off,
  once,
  pid,
  platform,
  ppid,
  prependListener,
  prependOnceListener,
  rawListeners,
  release,
  removeAllListeners,
  removeListener,
  report,
  resourceUsage,
  setMaxListeners,
  setSourceMapsEnabled,
  stderr,
  stdin,
  stdout,
  title,
  throwDeprecation,
  traceDeprecation,
  umask,
  uptime,
  version,
  versions,
  // @ts-expect-error old API
  domain,
  initgroups,
  moduleLoadList,
  reallyExit,
  openStdin,
  assert,
  binding,
  send,
  exitCode,
  channel,
  getegid,
  geteuid,
  getgid,
  getgroups,
  getuid,
  setegid,
  seteuid,
  setgid,
  setgroups,
  setuid,
  permission,
  mainModule,
  _events,
  _eventsCount,
  _exiting,
  _maxListeners,
  _debugEnd,
  _debugProcess,
  _fatalException,
  _getActiveHandles,
  _getActiveRequests,
  _kill,
  _preload_modules,
  _rawDebug,
  _startProfilerIdleNotifier,
  _stopProfilerIdleNotifier,
  _tickCallback,
  _disconnect,
  _handleQueue,
  _pendingMessage,
  _channel,
  _send,
  _linkedBinding
};
var process_default = _process;

// node_modules/wrangler/_virtual_unenv_global_polyfill-@cloudflare-unenv-preset-node-process
globalThis.process = process_default;

// node_modules/hono/dist/request/constants.js
var GET_MATCH_RESULT = /* @__PURE__ */ Symbol();

// node_modules/hono/dist/utils/buffer.js
var bufferToFormData = /* @__PURE__ */ __name((arrayBuffer, contentType) => {
  return new Response(arrayBuffer, { headers: { "Content-Type": contentType.replace(/^[^;]+/, (mediaType) => mediaType.toLowerCase()) } }).formData();
}, "bufferToFormData");

// node_modules/hono/dist/utils/body.js
var MAX_NESTED_OBJECTS = 1e4;
var isRawRequest = /* @__PURE__ */ __name((request) => "headers" in request, "isRawRequest");
var parseBody = /* @__PURE__ */ __name(async (request, options = /* @__PURE__ */ Object.create(null)) => {
  const { all = false, dot = false } = options;
  const mediaType = (isRawRequest(request) ? request.headers : request.raw.headers).get("Content-Type")?.split(";")[0].trim().toLowerCase();
  if (mediaType === "multipart/form-data" || mediaType === "application/x-www-form-urlencoded") return parseFormData(request, {
    all,
    dot
  });
  return {};
}, "parseBody");
async function parseFormData(request, options) {
  if (!isRawRequest(request) && request.bodyCache.formData) return convertFormDataToBodyData(await request.bodyCache.formData, options);
  const headers = isRawRequest(request) ? request.headers : request.raw.headers;
  const arrayBuffer = await request.arrayBuffer();
  const formDataPromise = bufferToFormData(arrayBuffer, headers.get("Content-Type") || "");
  if (!isRawRequest(request)) request.bodyCache.formData = formDataPromise;
  const formData = await formDataPromise;
  if (formData) return convertFormDataToBodyData(formData, options);
  return {};
}
__name(parseFormData, "parseFormData");
function convertFormDataToBodyData(formData, options) {
  const form = /* @__PURE__ */ Object.create(null);
  const nestingState = { count: 0 };
  formData.forEach((value, key) => {
    if (!(options.all || key.endsWith("[]"))) form[key] = value;
    else handleParsingAllValues(form, key, value);
  });
  if (options.dot) Object.entries(form).forEach(([key, value]) => {
    if (key.includes(".")) {
      handleParsingNestedValues(form, key, value, nestingState);
      delete form[key];
    }
  });
  return form;
}
__name(convertFormDataToBodyData, "convertFormDataToBodyData");
var handleParsingAllValues = /* @__PURE__ */ __name((form, key, value) => {
  if (form[key] !== void 0) {
    if (Array.isArray(form[key])) form[key].push(value);
    else form[key] = [form[key], value];
  } else if (!key.endsWith("[]")) form[key] = value;
  else form[key] = [value];
}, "handleParsingAllValues");
var handleParsingNestedValues = /* @__PURE__ */ __name((form, key, value, state) => {
  if (/(?:^|\.)__proto__\./.test(key)) return;
  let nestedForm = form;
  const keys = key.split(".", 34);
  if (keys.length > 33) throwNestingLimitExceeded();
  keys.forEach((key2, index) => {
    if (index === keys.length - 1) nestedForm[key2] = value;
    else {
      if (!nestedForm[key2] || typeof nestedForm[key2] !== "object" || Array.isArray(nestedForm[key2]) || nestedForm[key2] instanceof File) {
        if (state.count++ >= MAX_NESTED_OBJECTS) throwNestingLimitExceeded();
        nestedForm[key2] = /* @__PURE__ */ Object.create(null);
      }
      nestedForm = nestedForm[key2];
    }
  });
}, "handleParsingNestedValues");
var throwNestingLimitExceeded = /* @__PURE__ */ __name(() => {
  throw new Error("Nesting limit exceeded");
}, "throwNestingLimitExceeded");

// node_modules/hono/dist/utils/url.js
var splitPath = /* @__PURE__ */ __name((path) => {
  const paths = path.split("/");
  if (paths[0] === "") paths.shift();
  return paths;
}, "splitPath");
var splitRoutingPath = /* @__PURE__ */ __name((routePath) => {
  const { groups, path } = extractGroupsFromPath(routePath);
  const paths = splitPath(path);
  return replaceGroupMarks(paths, groups);
}, "splitRoutingPath");
var extractGroupsFromPath = /* @__PURE__ */ __name((path) => {
  const groups = [];
  path = path.replace(/\{[^}]+\}/g, (match2, index) => {
    const mark = `@${index}`;
    groups.push([mark, match2]);
    return mark;
  });
  return {
    groups,
    path
  };
}, "extractGroupsFromPath");
var replaceGroupMarks = /* @__PURE__ */ __name((paths, groups) => {
  for (let i = groups.length - 1; i >= 0; i--) {
    const [mark] = groups[i];
    for (let j = paths.length - 1; j >= 0; j--) if (paths[j].includes(mark)) {
      paths[j] = paths[j].replace(mark, groups[i][1]);
      break;
    }
  }
  return paths;
}, "replaceGroupMarks");
var patternCache = {};
var getPattern = /* @__PURE__ */ __name((label, next) => {
  if (label === "*") return "*";
  const match2 = label.match(/^\:([^\{\}]+)(?:\{(.+)\})?$/);
  if (match2) {
    const cacheKey = `${label}#${next}`;
    if (!patternCache[cacheKey]) {
      if (match2[2]) patternCache[cacheKey] = next && next[0] !== ":" && next[0] !== "*" ? [
        cacheKey,
        match2[1],
        new RegExp(`^${match2[2]}(?=/${next})`)
      ] : [
        label,
        match2[1],
        new RegExp(`^${match2[2]}$`)
      ];
      else patternCache[cacheKey] = [
        label,
        match2[1],
        true
      ];
    }
    return patternCache[cacheKey];
  }
  return null;
}, "getPattern");
var tryDecode = /* @__PURE__ */ __name((str, decoder) => {
  try {
    return decoder(str);
  } catch {
    return str.replace(/(?:%[0-9A-Fa-f]{2})+/g, (match2) => {
      try {
        return decoder(match2);
      } catch {
        return match2;
      }
    });
  }
}, "tryDecode");
var tryDecodeURI = /* @__PURE__ */ __name((str) => tryDecode(str, decodeURI), "tryDecodeURI");
var getPath = /* @__PURE__ */ __name((request) => {
  const url = request.url;
  const start = url.indexOf("/", url.indexOf(":") + 4);
  let i = start;
  for (; i < url.length; i++) {
    const charCode = url.charCodeAt(i);
    if (charCode === 37) {
      const queryIndex = url.indexOf("?", i);
      const hashIndex = url.indexOf("#", i);
      const end = queryIndex === -1 ? hashIndex === -1 ? void 0 : hashIndex : hashIndex === -1 ? queryIndex : Math.min(queryIndex, hashIndex);
      const path = url.slice(start, end);
      return tryDecodeURI(path.includes("%25") ? path.replace(/%25/g, "%2525") : path);
    } else if (charCode === 63 || charCode === 35) break;
  }
  return url.slice(start, i);
}, "getPath");
var getPathNoStrict = /* @__PURE__ */ __name((request) => {
  const result = getPath(request);
  return result.length > 1 && result.at(-1) === "/" ? result.slice(0, -1) : result;
}, "getPathNoStrict");
var mergePath = /* @__PURE__ */ __name((base, sub, ...rest) => {
  if (rest.length) sub = mergePath(sub, ...rest);
  return `${base?.[0] === "/" ? "" : "/"}${base}${sub === "/" ? "" : `${base?.at(-1) === "/" ? "" : "/"}${sub?.[0] === "/" ? sub.slice(1) : sub}`}`;
}, "mergePath");
var checkOptionalParameter = /* @__PURE__ */ __name((path) => {
  if (path.charCodeAt(path.length - 1) !== 63 || !path.includes(":")) return null;
  const segments = path.split("/");
  const results = [];
  let basePath = "";
  segments.forEach((segment) => {
    if (segment !== "" && !/\:/.test(segment)) basePath += "/" + segment;
    else if (/\:/.test(segment)) {
      if (segment.charCodeAt(segment.length - 1) === 63) {
        if (results.length === 0 && basePath === "") results.push("/");
        else results.push(basePath);
        const optionalSegment = segment.slice(0, -1);
        basePath += "/" + optionalSegment;
        results.push(basePath);
      } else basePath += "/" + segment;
    }
  });
  return results.filter((v, i, a) => a.indexOf(v) === i);
}, "checkOptionalParameter");
var tryDecodeURIComponent = /* @__PURE__ */ __name((str) => str.indexOf("%") !== -1 ? tryDecode(str, decodeURIComponent_) : str, "tryDecodeURIComponent");
var _decodeURI = /* @__PURE__ */ __name((value) => {
  if (value.indexOf("+") !== -1) value = value.replace(/\+/g, " ");
  return tryDecodeURIComponent(value);
}, "_decodeURI");
var _getQueryParam = /* @__PURE__ */ __name((url, key, multiple) => {
  const hashIndex = url.indexOf("#", 8);
  if (hashIndex !== -1) url = url.slice(0, hashIndex);
  let encoded;
  if (!multiple && key && key.indexOf("%") === -1 && key.indexOf("+") === -1) {
    let keyIndex2 = url.indexOf("?", 8);
    if (keyIndex2 === -1) return;
    if (!url.startsWith(key, keyIndex2 + 1)) keyIndex2 = url.indexOf(`&${key}`, keyIndex2 + 1);
    while (keyIndex2 !== -1) {
      const trailingKeyCode = url.charCodeAt(keyIndex2 + key.length + 1);
      if (trailingKeyCode === 61) {
        const valueIndex = keyIndex2 + key.length + 2;
        const endIndex = url.indexOf("&", valueIndex);
        return _decodeURI(url.slice(valueIndex, endIndex === -1 ? void 0 : endIndex));
      } else if (trailingKeyCode == 38 || isNaN(trailingKeyCode)) return "";
      keyIndex2 = url.indexOf(`&${key}`, keyIndex2 + 1);
    }
    encoded = /[%+]/.test(url);
    if (!encoded) return;
  }
  const results = /* @__PURE__ */ Object.create(null);
  encoded ??= /[%+]/.test(url);
  let keyIndex = url.indexOf("?", 8);
  while (keyIndex !== -1) {
    const nextKeyIndex = url.indexOf("&", keyIndex + 1);
    let valueIndex = url.indexOf("=", keyIndex);
    if (valueIndex > nextKeyIndex && nextKeyIndex !== -1) valueIndex = -1;
    let name = url.slice(keyIndex + 1, valueIndex === -1 ? nextKeyIndex === -1 ? void 0 : nextKeyIndex : valueIndex);
    if (encoded) name = _decodeURI(name);
    keyIndex = nextKeyIndex;
    if (name === "") continue;
    let value;
    if (valueIndex === -1) value = "";
    else {
      value = url.slice(valueIndex + 1, nextKeyIndex === -1 ? void 0 : nextKeyIndex);
      if (encoded) value = _decodeURI(value);
    }
    if (multiple) {
      if (!(results[name] && Array.isArray(results[name]))) results[name] = [];
      results[name].push(value);
    } else results[name] ??= value;
  }
  return key ? results[key] : results;
}, "_getQueryParam");
var getQueryParam = _getQueryParam;
var getQueryParams = /* @__PURE__ */ __name((url, key) => {
  return _getQueryParam(url, key, true);
}, "getQueryParams");
var decodeURIComponent_ = decodeURIComponent;

// node_modules/hono/dist/request.js
var HonoRequest = class {
  static {
    __name(this, "HonoRequest");
  }
  /**
  * `.raw` can get the raw Request object.
  *
  * @see {@link https://hono.dev/docs/api/request#raw}
  *
  * @example
  * ```ts
  * // For Cloudflare Workers
  * app.post('/', async (c) => {
  *   const metadata = c.req.raw.cf?.hostMetadata?
  *   ...
  * })
  * ```
  */
  raw;
  #validatedData;
  #matchResult;
  routeIndex = 0;
  /**
  * `.path` can get the pathname of the request.
  *
  * @see {@link https://hono.dev/docs/api/request#path}
  *
  * @example
  * ```ts
  * app.get('/about/me', (c) => {
  *   const pathname = c.req.path // `/about/me`
  * })
  * ```
  */
  path;
  bodyCache = {};
  constructor(request, path = "/", matchResult = [[]]) {
    this.raw = request;
    this.path = path;
    this.#matchResult = matchResult;
  }
  param(key) {
    return key ? this.#getDecodedParam(key) : this.#getAllDecodedParams();
  }
  #getDecodedParam(key) {
    const paramKey = this.#matchResult[0][this.routeIndex]?.[1][key];
    const param = this.#getParamValue(paramKey);
    return param && tryDecodeURIComponent(param);
  }
  #getAllDecodedParams() {
    const decoded = {};
    const keys = Object.keys(this.#matchResult[0][this.routeIndex]?.[1] ?? {});
    for (const key of keys) {
      const value = this.#getParamValue(this.#matchResult[0][this.routeIndex][1][key]);
      if (value !== void 0) decoded[key] = tryDecodeURIComponent(value);
    }
    return decoded;
  }
  #getParamValue(paramKey) {
    return this.#matchResult[1] ? this.#matchResult[1][paramKey] : paramKey;
  }
  query(key) {
    return getQueryParam(this.url, key);
  }
  queries(key) {
    return getQueryParams(this.url, key);
  }
  header(name) {
    if (name) return this.raw.headers.get(name) ?? void 0;
    const headerData = /* @__PURE__ */ Object.create(null);
    this.raw.headers.forEach((value, key) => {
      headerData[key] = value;
    });
    return headerData;
  }
  async parseBody(options) {
    return parseBody(this, options);
  }
  #cachedBody = /* @__PURE__ */ __name((key) => {
    const { bodyCache, raw: raw2 } = this;
    const cachedBody = bodyCache[key];
    if (cachedBody) return cachedBody;
    for (const anyCachedKey in bodyCache) return bodyCache[anyCachedKey].then((body) => {
      if (anyCachedKey === "json") body = JSON.stringify(body);
      const contentType = anyCachedKey === "formData" ? void 0 : raw2.headers.get("content-type");
      return new Response(body, { headers: contentType ? { "Content-Type": contentType } : void 0 })[key]();
    });
    return bodyCache[key] = raw2[key]();
  }, "#cachedBody");
  /**
  * `.json()` can parse Request body of type `application/json`
  *
  * @see {@link https://hono.dev/docs/api/request#json}
  *
  * @example
  * ```ts
  * app.post('/entry', async (c) => {
  *   const body = await c.req.json()
  * })
  * ```
  */
  json() {
    return this.#cachedBody("text").then((text) => JSON.parse(text));
  }
  /**
  * `.text()` can parse Request body of type `text/plain`
  *
  * @see {@link https://hono.dev/docs/api/request#text}
  *
  * @example
  * ```ts
  * app.post('/entry', async (c) => {
  *   const body = await c.req.text()
  * })
  * ```
  */
  text() {
    return this.#cachedBody("text");
  }
  /**
  * `.arrayBuffer()` parse Request body as an `ArrayBuffer`
  *
  * @see {@link https://hono.dev/docs/api/request#arraybuffer}
  *
  * @example
  * ```ts
  * app.post('/entry', async (c) => {
  *   const body = await c.req.arrayBuffer()
  * })
  * ```
  */
  arrayBuffer() {
    return this.#cachedBody("arrayBuffer");
  }
  /**
  * `.bytes()` parses the request body as a `Uint8Array`.
  *
  * @see {@link https://hono.dev/docs/api/request#bytes}
  *
  * @example
  * ```ts
  * app.post('/entry', async (c) => {
  *   const body = await c.req.bytes()
  * })
  * ```
  */
  bytes() {
    return this.#cachedBody("arrayBuffer").then((buffer) => new Uint8Array(buffer));
  }
  /**
  * Parses the request body as a `Blob`.
  * @example
  * ```ts
  * app.post('/entry', async (c) => {
  *   const body = await c.req.blob();
  * });
  * ```
  * @see https://hono.dev/docs/api/request#blob
  */
  blob() {
    return this.#cachedBody("blob");
  }
  /**
  * Parses the request body as `FormData`.
  * @example
  * ```ts
  * app.post('/entry', async (c) => {
  *   const body = await c.req.formData();
  * });
  * ```
  * @see https://hono.dev/docs/api/request#formdata
  */
  formData() {
    return this.#cachedBody("formData");
  }
  /**
  * Adds validated data to the request.
  *
  * @param target - The target of the validation.
  * @param data - The validated data to add.
  */
  addValidatedData(target, data) {
    (this.#validatedData ??= {})[target] = data;
  }
  valid(target) {
    return this.#validatedData?.[target];
  }
  /**
  * `.url` can get the request url strings.
  *
  * @see {@link https://hono.dev/docs/api/request#url}
  *
  * @example
  * ```ts
  * app.get('/about/me', (c) => {
  *   const url = c.req.url // `http://localhost:8787/about/me`
  *   ...
  * })
  * ```
  */
  get url() {
    return this.raw.url;
  }
  /**
  * `.method` can get the method name of the request.
  *
  * @see {@link https://hono.dev/docs/api/request#method}
  *
  * @example
  * ```ts
  * app.get('/about/me', (c) => {
  *   const method = c.req.method // `GET`
  * })
  * ```
  */
  get method() {
    return this.raw.method;
  }
  get [GET_MATCH_RESULT]() {
    return this.#matchResult;
  }
  /**
  * `.matchedRoutes` can return a matched route in the handler
  *
  * @deprecated
  *
  * Use matchedRoutes helper defined in "hono/route" instead.
  *
  * @see {@link https://hono.dev/docs/api/request#matchedroutes}
  *
  * @example
  * ```ts
  * app.use('*', async function logger(c, next) {
  *   await next()
  *   c.req.matchedRoutes.forEach(({ handler, method, path }, i) => {
  *     const name = handler.name || (handler.length < 2 ? '[handler]' : '[middleware]')
  *     console.log(
  *       method,
  *       ' ',
  *       path,
  *       ' '.repeat(Math.max(10 - path.length, 0)),
  *       name,
  *       i === c.req.routeIndex ? '<- respond from here' : ''
  *     )
  *   })
  * })
  * ```
  */
  get matchedRoutes() {
    return this.#matchResult[0].map(([[, route]]) => route);
  }
  /**
  * `.routePath` can retrieve the path registered within the handler
  *
  * @deprecated
  *
  * Use routePath helper defined in "hono/route" instead.
  *
  * @see {@link https://hono.dev/docs/api/request#routepath}
  *
  * @example
  * ```ts
  * app.get('/posts/:id', (c) => {
  *   return c.json({ path: c.req.routePath })
  * })
  * ```
  */
  get routePath() {
    return this.#matchResult[0].map(([[, route]]) => route)[this.routeIndex].path;
  }
};

// node_modules/hono/dist/utils/html.js
var HtmlEscapedCallbackPhase = {
  Stringify: 1,
  BeforeStream: 2,
  Stream: 3
};
var raw = /* @__PURE__ */ __name((value, callbacks) => {
  const escapedString = new String(value);
  escapedString.isEscaped = true;
  escapedString.callbacks = callbacks;
  return escapedString;
}, "raw");
var resolveCallback = /* @__PURE__ */ __name(async (str, phase, preserveCallbacks, context, buffer) => {
  if (typeof str === "object" && !(str instanceof String)) {
    if (!(str instanceof Promise)) str = str.toString();
    if (str instanceof Promise) str = await str;
  }
  const callbacks = str.callbacks;
  if (!callbacks?.length) return Promise.resolve(str);
  if (buffer) buffer[0] += str;
  else buffer = [str];
  const resStr = Promise.all(callbacks.map((c) => c({
    phase,
    buffer,
    context
  }))).then((res) => Promise.all(res.filter(Boolean).map((str2) => resolveCallback(str2, phase, false, context, buffer))).then(() => buffer[0]));
  if (preserveCallbacks) return raw(await resStr, callbacks);
  else return resStr;
}, "resolveCallback");

// node_modules/hono/dist/context.js
var TEXT_PLAIN = "text/plain; charset=UTF-8";
var setDefaultContentType = /* @__PURE__ */ __name((contentType, headers) => {
  return {
    "Content-Type": contentType,
    ...headers
  };
}, "setDefaultContentType");
var createResponseInstance = /* @__PURE__ */ __name((body, init) => new Response(body, init), "createResponseInstance");
var Context = class {
  static {
    __name(this, "Context");
  }
  #rawRequest;
  #req;
  /**
  * `.env` can get bindings (environment variables, secrets, KV namespaces, D1 database, R2 bucket etc.) in Cloudflare Workers.
  *
  * @see {@link https://hono.dev/docs/api/context#env}
  *
  * @example
  * ```ts
  * // Environment object for Cloudflare Workers
  * app.get('*', async c => {
  *   const counter = c.env.COUNTER
  * })
  * ```
  */
  env = {};
  #var;
  finalized = false;
  /**
  * `.error` can get the error object from the middleware if the Handler throws an error.
  *
  * @see {@link https://hono.dev/docs/api/context#error}
  *
  * @example
  * ```ts
  * app.use('*', async (c, next) => {
  *   await next()
  *   if (c.error) {
  *     // do something...
  *   }
  * })
  * ```
  */
  error;
  #status;
  #executionCtx;
  #res;
  #layout;
  #renderer;
  #notFoundHandler;
  #preparedHeaders;
  #matchResult;
  #path;
  /**
  * Creates an instance of the Context class.
  *
  * @param req - The Request object.
  * @param options - Optional configuration options for the context.
  */
  constructor(req, options) {
    this.#rawRequest = req;
    if (options) {
      this.#executionCtx = options.executionCtx;
      this.env = options.env;
      this.#notFoundHandler = options.notFoundHandler;
      this.#path = options.path;
      this.#matchResult = options.matchResult;
    }
  }
  /**
  * `.req` is the instance of {@link HonoRequest}.
  */
  get req() {
    this.#req ??= new HonoRequest(this.#rawRequest, this.#path, this.#matchResult);
    return this.#req;
  }
  /**
  * @see {@link https://hono.dev/docs/api/context#event}
  * The FetchEvent associated with the current request.
  *
  * @throws Will throw an error if the context does not have a FetchEvent.
  */
  get event() {
    if (this.#executionCtx && "respondWith" in this.#executionCtx) return this.#executionCtx;
    else throw Error("This context has no FetchEvent");
  }
  /**
  * @see {@link https://hono.dev/docs/api/context#executionctx}
  * The ExecutionContext associated with the current request.
  *
  * @throws Will throw an error if the context does not have an ExecutionContext.
  */
  get executionCtx() {
    if (this.#executionCtx) return this.#executionCtx;
    else throw Error("This context has no ExecutionContext");
  }
  /**
  * @see {@link https://hono.dev/docs/api/context#res}
  * The Response object for the current request.
  */
  get res() {
    return this.#res ||= createResponseInstance(null, { headers: this.#preparedHeaders ??= new Headers() });
  }
  /**
  * Sets the Response object for the current request.
  *
  * @param _res - The Response object to set.
  */
  set res(_res) {
    if (this.#res && _res) {
      _res = createResponseInstance(_res.body, _res);
      for (const [k, v] of this.#res.headers.entries()) {
        if (k === "content-type") continue;
        if (k === "set-cookie") {
          const cookies = this.#res.headers.getSetCookie();
          _res.headers.delete("set-cookie");
          for (const cookie of cookies) _res.headers.append("set-cookie", cookie);
        } else _res.headers.set(k, v);
      }
    }
    this.#res = _res;
    this.finalized = true;
  }
  /**
  * `.render()` can create a response within a layout.
  *
  * @see {@link https://hono.dev/docs/api/context#render-setrenderer}
  *
  * @example
  * ```ts
  * app.get('/', (c) => {
  *   return c.render('Hello!')
  * })
  * ```
  */
  render = /* @__PURE__ */ __name((...args) => {
    this.#renderer ??= (content) => this.html(content);
    return this.#renderer(...args);
  }, "render");
  /**
  * Sets the layout for the response.
  *
  * @param layout - The layout to set.
  * @returns The layout function.
  */
  setLayout = /* @__PURE__ */ __name((layout) => this.#layout = layout, "setLayout");
  /**
  * Gets the current layout for the response.
  *
  * @returns The current layout function.
  */
  getLayout = /* @__PURE__ */ __name(() => this.#layout, "getLayout");
  /**
  * `.setRenderer()` can set the layout in the custom middleware.
  *
  * @see {@link https://hono.dev/docs/api/context#render-setrenderer}
  *
  * @example
  * ```tsx
  * app.use('*', async (c, next) => {
  *   c.setRenderer((content) => {
  *     return c.html(
  *       <html>
  *         <body>
  *           <p>{content}</p>
  *         </body>
  *       </html>
  *     )
  *   })
  *   await next()
  * })
  * ```
  */
  setRenderer = /* @__PURE__ */ __name((renderer) => {
    this.#renderer = renderer;
  }, "setRenderer");
  /**
  * `.header()` can set headers.
  *
  * @see {@link https://hono.dev/docs/api/context#header}
  *
  * @example
  * ```ts
  * app.get('/welcome', (c) => {
  *   // Set headers
  *   c.header('X-Message', 'Hello!')
  *   c.header('Content-Type', 'text/plain')
  *
  *   // Append multiple headers using the append option (e.g. Vary)
  *   c.header('Vary', 'Accept-Encoding', { append: true })
  *   c.header('Vary', 'User-Agent', { append: true })
  *
  *   return c.body('Thank you for coming')
  * })
  * ```
  */
  header = /* @__PURE__ */ __name((name, value, options) => {
    if (this.finalized) this.#res = createResponseInstance(this.#res.body, this.#res);
    const headers = this.#res ? this.#res.headers : this.#preparedHeaders ??= new Headers();
    if (value === void 0) headers.delete(name);
    else if (options?.append) headers.append(name, value);
    else headers.set(name, value);
  }, "header");
  status = /* @__PURE__ */ __name((status) => {
    this.#status = status;
  }, "status");
  /**
  * `.set()` can set the value specified by the key.
  *
  * @see {@link https://hono.dev/docs/api/context#set-get}
  *
  * @example
  * ```ts
  * app.use('*', async (c, next) => {
  *   c.set('message', 'Hono is hot!!')
  *   await next()
  * })
  * ```
  */
  set = /* @__PURE__ */ __name((key, value) => {
    this.#var ??= /* @__PURE__ */ new Map();
    this.#var.set(key, value);
  }, "set");
  /**
  * `.get()` can use the value specified by the key.
  *
  * @see {@link https://hono.dev/docs/api/context#set-get}
  *
  * @example
  * ```ts
  * app.get('/', (c) => {
  *   const message = c.get('message')
  *   return c.text(`The message is "${message}"`)
  * })
  * ```
  */
  get = /* @__PURE__ */ __name((key) => {
    return this.#var ? this.#var.get(key) : void 0;
  }, "get");
  /**
  * `.var` can access the value of a variable.
  *
  * @see {@link https://hono.dev/docs/api/context#var}
  *
  * @example
  * ```ts
  * const result = c.var.client.oneMethod()
  * ```
  */
  get var() {
    if (!this.#var) return {};
    return Object.fromEntries(this.#var);
  }
  #newResponse(data, arg, headers) {
    let responseHeaders = this.#res ? new Headers(this.#res.headers) : this.#preparedHeaders;
    if (typeof arg === "object" && arg.headers) {
      responseHeaders ??= new Headers();
      for (const [key, value] of new Headers(arg.headers)) if (key === "set-cookie") responseHeaders.append(key, value);
      else responseHeaders.set(key, value);
    }
    if (headers) {
      if (!responseHeaders) {
        let count = 0;
        for (const k in headers) if (++count > 1 || typeof headers[k] !== "string") {
          responseHeaders = new Headers();
          break;
        }
      }
      if (responseHeaders) for (const k in headers) {
        const v = headers[k];
        if (typeof v === "string") responseHeaders.set(k, v);
        else {
          responseHeaders.delete(k);
          for (const v2 of v) responseHeaders.append(k, v2);
        }
      }
    }
    const status = typeof arg === "number" ? arg : arg?.status ?? this.#status;
    return createResponseInstance(data, {
      status,
      headers: responseHeaders ?? headers
    });
  }
  newResponse = /* @__PURE__ */ __name((...args) => this.#newResponse(...args), "newResponse");
  /**
  * `.body()` can return the HTTP response.
  * You can set headers with `.header()` and set HTTP status code with `.status`.
  * This can also be set in `.text()`, `.json()` and so on.
  *
  * @see {@link https://hono.dev/docs/api/context#body}
  *
  * @example
  * ```ts
  * app.get('/welcome', (c) => {
  *   // Set headers
  *   c.header('X-Message', 'Hello!')
  *   c.header('Content-Type', 'text/plain')
  *   // Set HTTP status code
  *   c.status(201)
  *
  *   // Return the response body
  *   return c.body('Thank you for coming')
  * })
  * ```
  */
  body = /* @__PURE__ */ __name((data, arg, headers) => this.#newResponse(data, arg, headers), "body");
  /**
  * `.text()` can render text as `Content-Type:text/plain`.
  *
  * @see {@link https://hono.dev/docs/api/context#text}
  *
  * @example
  * ```ts
  * app.get('/say', (c) => {
  *   return c.text('Hello!')
  * })
  * ```
  */
  text = /* @__PURE__ */ __name((text, arg, headers) => {
    return !this.#preparedHeaders && !this.#status && !arg && !headers && !this.finalized ? new Response(text) : this.#newResponse(text, arg, setDefaultContentType(TEXT_PLAIN, headers));
  }, "text");
  /**
  * `.json()` can render JSON as `Content-Type:application/json`.
  *
  * @see {@link https://hono.dev/docs/api/context#json}
  *
  * @example
  * ```ts
  * app.get('/api', (c) => {
  *   return c.json({ message: 'Hello!' })
  * })
  * ```
  */
  json = /* @__PURE__ */ __name((object, arg, headers) => {
    return this.#newResponse(JSON.stringify(object), arg, setDefaultContentType("application/json", headers));
  }, "json");
  html = /* @__PURE__ */ __name((html, arg, headers) => {
    const res = /* @__PURE__ */ __name((html2) => this.#newResponse(html2, arg, setDefaultContentType("text/html; charset=UTF-8", headers)), "res");
    return typeof html === "object" ? resolveCallback(html, HtmlEscapedCallbackPhase.Stringify, false, {}).then(res) : res(html);
  }, "html");
  /**
  * `.redirect()` can Redirect, default status code is 302.
  *
  * @see {@link https://hono.dev/docs/api/context#redirect}
  *
  * @example
  * ```ts
  * app.get('/redirect', (c) => {
  *   return c.redirect('/')
  * })
  * app.get('/redirect-permanently', (c) => {
  *   return c.redirect('/', 301)
  * })
  * ```
  */
  redirect = /* @__PURE__ */ __name((location, status) => {
    const locationString = String(location);
    this.header("Location", !/[^\x00-\xFF]/.test(locationString) ? locationString : encodeURI(locationString));
    return this.newResponse(null, status ?? 302);
  }, "redirect");
  /**
  * `.notFound()` can return the Not Found Response.
  *
  * @see {@link https://hono.dev/docs/api/context#notfound}
  *
  * @example
  * ```ts
  * app.get('/notfound', (c) => {
  *   return c.notFound()
  * })
  * ```
  */
  notFound = /* @__PURE__ */ __name(() => {
    this.#notFoundHandler ??= () => createResponseInstance();
    return this.#notFoundHandler(this);
  }, "notFound");
};

// node_modules/hono/dist/compose.js
var compose = /* @__PURE__ */ __name((middleware, onError, onNotFound) => {
  return (context, next) => {
    let index = -1;
    return dispatch(0);
    async function dispatch(i) {
      if (i <= index) throw new Error("next() called multiple times");
      index = i;
      let res;
      let isError = false;
      let handler;
      if (middleware[i]) {
        handler = middleware[i][0][0];
        context.req.routeIndex = i;
      } else handler = i === middleware.length && next || void 0;
      if (handler) try {
        res = await handler(context, () => dispatch(i + 1));
      } catch (err) {
        if (err instanceof Error && onError) {
          context.error = err;
          res = await onError(err, context);
          isError = true;
        } else throw err;
      }
      else if (context.finalized === false && onNotFound) res = await onNotFound(context);
      if (res && (context.finalized === false || isError)) context.res = res;
      return context;
    }
    __name(dispatch, "dispatch");
  };
}, "compose");

// node_modules/hono/dist/router.js
var METHODS = [
  "get",
  "post",
  "put",
  "delete",
  "options",
  "patch",
  "query"
];
var MESSAGE_MATCHER_IS_ALREADY_BUILT = "Can not add a route since the matcher is already built.";
var UnsupportedPathError = class extends Error {
  static {
    __name(this, "UnsupportedPathError");
  }
};

// node_modules/hono/dist/utils/constants.js
var COMPOSED_HANDLER = "__COMPOSED_HANDLER";

// node_modules/hono/dist/hono-base.js
var notFoundHandler = /* @__PURE__ */ __name((c) => {
  return c.text("404 Not Found", 404);
}, "notFoundHandler");
var errorHandler = /* @__PURE__ */ __name((err, c) => {
  if ("getResponse" in err) {
    const res = err.getResponse();
    return c.newResponse(res.body, res);
  }
  console.error(err);
  return c.text("Internal Server Error", 500);
}, "errorHandler");
var Hono = class Hono2 {
  static {
    __name(this, "Hono");
  }
  get;
  post;
  put;
  delete;
  options;
  patch;
  query;
  all;
  on;
  use;
  router;
  getPath;
  _basePath = "/";
  #path = "/";
  routes = [];
  constructor(options = {}) {
    [...METHODS, "all"].forEach((method) => {
      this[method] = (args1, ...args) => {
        const methodName = method.toUpperCase();
        if (typeof args1 === "string") this.#path = args1;
        else this.#addRoute(methodName, this.#path, args1);
        args.forEach((handler) => {
          this.#addRoute(methodName, this.#path, handler);
        });
        return this;
      };
    });
    this.on = (method, path, ...handlers) => {
      for (const p of [path].flat()) {
        this.#path = p;
        for (const m of [method].flat()) {
          const methodName = m.toUpperCase();
          for (const handler of handlers) this.#addRoute(methodName, this.#path, handler);
        }
      }
      return this;
    };
    this.use = (arg1, ...handlers) => {
      if (typeof arg1 === "string") this.#path = arg1;
      else {
        this.#path = "*";
        handlers.unshift(arg1);
      }
      handlers.forEach((handler) => {
        this.#addRoute("ALL", this.#path, handler);
      });
      return this;
    };
    const { strict, ...optionsWithoutStrict } = options;
    Object.assign(this, optionsWithoutStrict);
    this.getPath = strict ?? true ? options.getPath ?? getPath : getPathNoStrict;
  }
  #clone() {
    const clone = new Hono2({
      router: this.router,
      getPath: this.getPath
    });
    clone.errorHandler = this.errorHandler;
    clone.#notFoundHandler = this.#notFoundHandler;
    clone.routes = this.routes;
    return clone;
  }
  #notFoundHandler = notFoundHandler;
  errorHandler = errorHandler;
  /**
  * `.route()` allows grouping other Hono instance in routes.
  *
  * @see {@link https://hono.dev/docs/api/routing#grouping}
  *
  * @param {string} path - base Path
  * @param {Hono} app - other Hono instance
  * @returns {Hono} routed Hono instance
  *
  * @example
  * ```ts
  * const app = new Hono()
  * const app2 = new Hono()
  *
  * app2.get("/user", (c) => c.text("user"))
  * app.route("/api", app2) // GET /api/user
  * ```
  */
  route(path, app2) {
    const subApp = this.basePath(path);
    app2.routes.map((r) => {
      let handler;
      if (app2.errorHandler === errorHandler) handler = r.handler;
      else {
        handler = /* @__PURE__ */ __name(async (c, next) => (await compose([], app2.errorHandler)(c, () => r.handler(c, next))).res, "handler");
        handler[COMPOSED_HANDLER] = r.handler;
      }
      subApp.#addRoute(r.method, r.path, handler, r.basePath);
    });
    return this;
  }
  /**
  * `.basePath()` allows base paths to be specified.
  *
  * @see {@link https://hono.dev/docs/api/routing#base-path}
  *
  * @param {string} path - base Path
  * @returns {Hono} changed Hono instance
  *
  * @example
  * ```ts
  * const api = new Hono().basePath('/api')
  * ```
  */
  basePath(path) {
    const subApp = this.#clone();
    subApp._basePath = mergePath(this._basePath, path);
    return subApp;
  }
  /**
  * `.onError()` handles an error and returns a customized Response.
  *
  * @see {@link https://hono.dev/docs/api/hono#error-handling}
  *
  * @param {ErrorHandler} handler - request Handler for error
  * @returns {Hono} changed Hono instance
  *
  * @example
  * ```ts
  * app.onError((err, c) => {
  *   console.error(`${err}`)
  *   return c.text('Custom Error Message', 500)
  * })
  * ```
  */
  onError = /* @__PURE__ */ __name((handler) => {
    this.errorHandler = handler;
    return this;
  }, "onError");
  /**
  * `.notFound()` allows you to customize a Not Found Response.
  *
  * @see {@link https://hono.dev/docs/api/hono#not-found}
  *
  * @param {NotFoundHandler} handler - request handler for not-found
  * @returns {Hono} changed Hono instance
  *
  * @example
  * ```ts
  * app.notFound((c) => {
  *   return c.text('Custom 404 Message', 404)
  * })
  * ```
  */
  notFound = /* @__PURE__ */ __name((handler) => {
    this.#notFoundHandler = handler;
    return this;
  }, "notFound");
  /**
  * `.mount()` allows you to mount applications built with other frameworks into your Hono application.
  *
  * @deprecated Use `mount()` from `hono/mount` instead. `.mount()` will be removed in v5.
  *
  * @see {@link https://hono.dev/docs/api/hono#mount}
  *
  * @param {string} path - base Path
  * @param {Function} applicationHandler - other Request Handler
  * @param {MountOptions} [options] - options of `.mount()`
  * @returns {Hono} mounted Hono instance
  *
  * @example
  * ```ts
  * import { Router as IttyRouter } from 'itty-router'
  * import { Hono } from 'hono'
  * // Create itty-router application
  * const ittyRouter = IttyRouter()
  * // GET /itty-router/hello
  * ittyRouter.get('/hello', () => new Response('Hello from itty-router'))
  *
  * const app = new Hono()
  * app.mount('/itty-router', ittyRouter.handle)
  * ```
  *
  * @example
  * ```ts
  * const app = new Hono()
  * // Send the request to another application without modification.
  * app.mount('/app', anotherApp, {
  *   replaceRequest: (req) => req,
  * })
  * ```
  */
  mount(path, applicationHandler, options) {
    let replaceRequest;
    let optionHandler;
    if (options) {
      if (typeof options === "function") optionHandler = options;
      else {
        optionHandler = options.optionHandler;
        if (options.replaceRequest === false) replaceRequest = /* @__PURE__ */ __name((request) => request, "replaceRequest");
        else replaceRequest = options.replaceRequest;
      }
    }
    const getOptions = optionHandler ? (c) => {
      const options2 = optionHandler(c);
      return Array.isArray(options2) ? options2 : [options2];
    } : (c) => {
      let executionContext = void 0;
      try {
        executionContext = c.executionCtx;
      } catch {
      }
      return [c.env, executionContext];
    };
    replaceRequest ||= (() => {
      const mergedPath = mergePath(this._basePath, path);
      const pathPrefixLength = mergedPath === "/" ? 0 : mergedPath.length;
      return (request) => {
        const url = new URL(request.url);
        url.pathname = this.getPath(request).slice(pathPrefixLength) || "/";
        return new Request(url, request);
      };
    })();
    const handler = /* @__PURE__ */ __name(async (c, next) => {
      const res = await applicationHandler(replaceRequest(c.req.raw), ...getOptions(c));
      if (res) return res;
      await next();
    }, "handler");
    this.#addRoute("ALL", mergePath(path, "*"), handler);
    return this;
  }
  #addRoute(method, path, handler, baseRoutePath) {
    path = mergePath(this._basePath, path);
    const r = {
      basePath: baseRoutePath !== void 0 ? mergePath(this._basePath, baseRoutePath) : this._basePath,
      path,
      method,
      handler
    };
    this.router.add(method, path, [handler, r]);
    this.routes.push(r);
  }
  #handleError(err, c) {
    if (err instanceof Error) return this.errorHandler(err, c);
    throw err;
  }
  #dispatch(request, executionCtx, env2, method) {
    if (method === "HEAD") return (async () => new Response(null, await this.#dispatch(request, executionCtx, env2, "GET")))();
    const path = this.getPath(request, { env: env2 });
    const matchResult = this.router.match(method, path);
    const c = new Context(request, {
      path,
      matchResult,
      env: env2,
      executionCtx,
      notFoundHandler: this.#notFoundHandler
    });
    if (matchResult[0].length === 1) {
      let res;
      try {
        res = matchResult[0][0][0][0](c, async () => {
          c.res = await this.#notFoundHandler(c);
        });
      } catch (err) {
        return this.#handleError(err, c);
      }
      return res instanceof Promise ? res.then((resolved) => resolved || (c.finalized ? c.res : this.#notFoundHandler(c))).catch((err) => this.#handleError(err, c)) : res ?? this.#notFoundHandler(c);
    }
    const composed = compose(matchResult[0], this.errorHandler, this.#notFoundHandler);
    return (async () => {
      try {
        const context = await composed(c);
        if (!context.finalized) throw new Error("Context is not finalized. Did you forget to return a Response object or `await next()`?");
        return context.res;
      } catch (err) {
        return this.#handleError(err, c);
      }
    })();
  }
  /**
  * `.fetch()` will be entry point of your app.
  *
  * @see {@link https://hono.dev/docs/api/hono#fetch}
  *
  * @param {Request} request - request Object of request
  * @param {Env} env - env Object
  * @param {ExecutionContext} executionCtx - context of execution
  * @returns {Response | Promise<Response>} response of request
  *
  */
  fetch = /* @__PURE__ */ __name((request, ...rest) => {
    return this.#dispatch(request, rest[1], rest[0], request.method);
  }, "fetch");
  /**
  * `.request()` is a useful method for testing.
  * You can pass a URL or pathname to send a GET request.
  * app will return a Response object.
  * ```ts
  * test('GET /hello is ok', async () => {
  *   const res = await app.request('/hello')
  *   expect(res.status).toBe(200)
  * })
  * ```
  * @see https://hono.dev/docs/api/hono#request
  */
  request = /* @__PURE__ */ __name((input, requestInit, Env, executionCtx) => {
    if (input instanceof Request) return this.fetch(requestInit ? new Request(input, requestInit) : input, Env, executionCtx);
    input = input.toString();
    return this.fetch(new Request(/^https?:\/\//.test(input) ? input : `http://localhost${mergePath("/", input)}`, requestInit), Env, executionCtx);
  }, "request");
  /**
  * `.fire()` automatically adds a global fetch event listener.
  * This can be useful for environments that adhere to the Service Worker API, such as non-ES module Cloudflare Workers.
  * @deprecated
  * Use `fire` from `hono/service-worker` instead.
  * ```ts
  * import { Hono } from 'hono'
  * import { fire } from 'hono/service-worker'
  *
  * const app = new Hono()
  * // ...
  * fire(app)
  * ```
  * @see https://hono.dev/docs/api/hono#fire
  * @see https://developer.mozilla.org/en-US/docs/Web/API/Service_Worker_API
  * @see https://developers.cloudflare.com/workers/reference/migrate-to-module-workers/
  */
  fire = /* @__PURE__ */ __name(() => {
    addEventListener("fetch", (event) => {
      event.respondWith(this.#dispatch(event.request, event, void 0, event.request.method));
    });
  }, "fire");
};

// node_modules/hono/dist/router/utils.js
var createNullObject = /* @__PURE__ */ __name(() => /* @__PURE__ */ Object.create(null), "createNullObject");

// node_modules/hono/dist/router/reg-exp-router/matcher.js
var emptyParam = [];
function match(method, path) {
  const matchers = this.buildAllMatchers();
  const match2 = /* @__PURE__ */ __name(((method2, path2) => {
    const matcher = matchers[method2] || matchers["ALL"];
    const staticMatch = matcher[2][path2];
    if (staticMatch) return staticMatch;
    const match3 = path2.match(matcher[0]);
    if (!match3) return [[], emptyParam];
    const index = match3.indexOf("", 1);
    return [matcher[1][index], match3];
  }), "match");
  this.match = match2;
  return match2(method, path);
}
__name(match, "match");

// node_modules/hono/dist/router/reg-exp-router/node.js
var LABEL_REG_EXP_STR = "[^/]+";
var TAIL_WILDCARD_REG_EXP_STR = "(?:|/.*)";
var PATH_ERROR = /* @__PURE__ */ Symbol();
var regExpMetaChars = /* @__PURE__ */ new Set(".\\+*[^]$()");
function compareKey(a, b) {
  if (a.length === 1) return b.length === 1 ? a < b ? -1 : 1 : -1;
  if (b.length === 1) return 1;
  if (a === ".*" || a === "(?:|/.*)") return b === "(?:|/.*)" ? -1 : 1;
  else if (b === ".*" || b === "(?:|/.*)") return -1;
  if (a === "[^/]+") return 1;
  else if (b === "[^/]+") return -1;
  return a.length === b.length ? a < b ? -1 : 1 : b.length - a.length;
}
__name(compareKey, "compareKey");
var Node = class Node2 {
  static {
    __name(this, "Node");
  }
  #index;
  #varIndex;
  #children = createNullObject();
  insert(tokens, index, paramMap, context, isStatic) {
    let node = this;
    for (let i = 0, len = tokens.length; i < len; i++) {
      const token = tokens[i];
      const pattern = token.length === 1 ? token === "*" ? i === len - 1 ? [
        "",
        "",
        ".*"
      ] : [
        "",
        "",
        LABEL_REG_EXP_STR
      ] : null : token === "/*" ? [
        "",
        "",
        TAIL_WILDCARD_REG_EXP_STR
      ] : token.match(/^\:([^\{\}]+)(?:\{(.+)\})?$/);
      let nextNode;
      if (pattern) {
        const name = pattern[1];
        let regexpStr = pattern[2] || "[^/]+";
        if (name && pattern[2]) {
          if (regexpStr === ".*") throw PATH_ERROR;
          regexpStr = regexpStr.replace(/^\((?!\?:)(?=[^)]+\)$)/, "(?:");
          if (/\((?!\?:)/.test(regexpStr)) throw PATH_ERROR;
          if (regexpStr.length === 1 && regExpMetaChars.has(regexpStr)) throw PATH_ERROR;
        }
        nextNode = node.#children[regexpStr];
        if (!nextNode) {
          if (regexpStr !== ".*" && regexpStr !== "(?:|/.*)") {
            for (const k in node.#children) if ((regexpStr.length > 1 || k.length > 1) && k !== ".*" && k !== "(?:|/.*)") throw PATH_ERROR;
          }
          nextNode = node.#children[regexpStr] = new Node2();
        }
        if (name !== "") {
          nextNode.#varIndex ??= context.varIndex++;
          paramMap.push([name, nextNode.#varIndex]);
        }
      } else {
        nextNode = node.#children[token];
        if (!nextNode) {
          for (const k in node.#children) if (k.length > 1 && k !== ".*" && k !== "(?:|/.*)") throw PATH_ERROR;
          nextNode = node.#children[token] = new Node2();
        }
      }
      node = nextNode;
    }
    if (node.#index !== void 0) throw PATH_ERROR;
    node.#index = isStatic ? -1 : index;
  }
  buildRegExpStr() {
    const strList = Object.keys(this.#children).sort(compareKey).map((k) => {
      const c = this.#children[k];
      const childStr = c.buildRegExpStr();
      return childStr === "" ? "" : (typeof c.#varIndex === "number" ? `(${k})@${c.#varIndex}` : regExpMetaChars.has(k) ? `\\${k}` : k) + childStr;
    }).filter(Boolean);
    if (typeof this.#index === "number" && this.#index !== -1) strList.unshift(`#${this.#index}`);
    if (strList.length === 0) return "";
    if (strList.length === 1) return strList[0];
    return "(?:" + strList.join("|") + ")";
  }
};

// node_modules/hono/dist/router/reg-exp-router/trie.js
var Trie = class {
  static {
    __name(this, "Trie");
  }
  #context = { varIndex: 0 };
  #root = new Node();
  #index = 0;
  paths = createNullObject();
  insert(path, isStatic) {
    if (isStatic) {
      this.#root.insert(path.split(""), 0, [], this.#context, true);
      return;
    }
    const paramAssoc = [];
    const groups = [];
    let markedPath = path;
    for (let i = 0; ; ) {
      let replaced = false;
      markedPath = markedPath.replace(/\{[^}]+\}/g, (m) => {
        const mark = `@\\${i}`;
        groups[i] = [mark, m];
        i++;
        replaced = true;
        return mark;
      });
      if (!replaced) break;
    }
    const tokens = markedPath.match(/(?::[^\/]+)|(?:\/\*$)|./g) || [];
    for (let i = groups.length - 1; i >= 0; i--) {
      const [mark] = groups[i];
      for (let j = tokens.length - 1; j >= 0; j--) if (tokens[j].indexOf(mark) !== -1) {
        tokens[j] = tokens[j].replace(mark, groups[i][1]);
        break;
      }
    }
    this.#root.insert(tokens, this.#index, paramAssoc, this.#context, false);
    this.paths[path] = [this.#index++, paramAssoc];
  }
  buildRegExp() {
    let regexp = this.#root.buildRegExpStr();
    if (regexp === "") return [
      /^$/,
      [],
      []
    ];
    let captureIndex = 0;
    const indexReplacementMap = [];
    const paramReplacementMap = [];
    regexp = regexp.replace(/#(\d+)|@(\d+)|\.\*\$/g, (_, handlerIndex, paramIndex) => {
      if (handlerIndex !== void 0) {
        indexReplacementMap[++captureIndex] = Number(handlerIndex);
        return "$()";
      }
      if (paramIndex !== void 0) {
        paramReplacementMap[Number(paramIndex)] = ++captureIndex;
        return "";
      }
      return "";
    });
    return [
      new RegExp(`^${regexp}`),
      indexReplacementMap,
      paramReplacementMap
    ];
  }
};

// node_modules/hono/dist/router/reg-exp-router/router.js
var wildcardRegExpCache = createNullObject();
function buildWildcardRegExp(path) {
  return wildcardRegExpCache[path] ??= new RegExp(`^${path.replace(/\/:[^/{}]+(?:\{\[\^\/]\+})?(?=[/{]|$)|\/?\*$|([.\\+*[^\]$()?{}|])/g, (match2, metaChar) => metaChar ? `\\${metaChar}` : match2 === "/*" ? TAIL_WILDCARD_REG_EXP_STR : match2 === "*" ? ".*" : `/:${LABEL_REG_EXP_STR}`)}$`);
}
__name(buildWildcardRegExp, "buildWildcardRegExp");
function findMiddleware(middleware, path) {
  for (const k of Object.keys(middleware).sort((a, b) => b.length - a.length)) if (buildWildcardRegExp(k).test(path)) return [...middleware[k]];
}
__name(findMiddleware, "findMiddleware");
var RegExpRouter = class {
  static {
    __name(this, "RegExpRouter");
  }
  name = "RegExpRouter";
  #middleware;
  #routes;
  #tries;
  constructor() {
    this.#middleware = { ["ALL"]: createNullObject() };
    this.#routes = { ["ALL"]: createNullObject() };
    this.#tries = { ["ALL"]: new Trie() };
  }
  #insertPath(method, path) {
    try {
      this.#tries[method].insert(path, !/\*|\/:/.test(path));
    } catch (e) {
      throw e === PATH_ERROR ? new UnsupportedPathError(path) : e;
    }
  }
  add(method, path, handler) {
    const middleware = this.#middleware;
    const routes = this.#routes;
    if (!middleware) throw new Error(MESSAGE_MATCHER_IS_ALREADY_BUILT);
    if (!middleware[method]) {
      this.#tries[method] = new Trie();
      for (const handlerMap of [middleware, routes]) {
        handlerMap[method] = createNullObject();
        for (const p in handlerMap["ALL"]) {
          handlerMap[method][p] = [...handlerMap["ALL"][p]];
          this.#insertPath(method, p);
        }
      }
    }
    if (path === "/*") path = "*";
    const methods = method === "ALL" ? Object.keys(middleware) : [method];
    if (/\*$/.test(path)) {
      const re = buildWildcardRegExp(path);
      for (const m of methods) if (!middleware[m][path]) {
        this.#insertPath(m, path);
        middleware[m][path] = findMiddleware(middleware[m], path) || findMiddleware(middleware["ALL"], path) || [];
      }
      for (const handlerMap of [middleware, routes]) for (const m of methods) for (const p in handlerMap[m]) re.test(p) && handlerMap[m][p].push([handler, path]);
      return;
    }
    const paths = checkOptionalParameter(path) || [path];
    for (const path2 of paths) for (const m of methods) {
      if (!routes[m][path2]) {
        this.#insertPath(m, path2);
        routes[m][path2] = findMiddleware(middleware[m], path2) || findMiddleware(middleware["ALL"], path2) || [];
      }
      routes[m][path2].push([handler, path2]);
    }
  }
  match = match;
  buildAllMatchers() {
    const matchers = createNullObject();
    for (const method of Object.keys(this.#routes)) matchers[method] = this.#buildMatcher(method);
    this.#middleware = this.#routes = this.#tries = void 0;
    wildcardRegExpCache = createNullObject();
    return matchers;
  }
  #buildMatcher(method) {
    const middleware = this.#middleware[method];
    const routes = this.#routes[method];
    const trie = this.#tries[method];
    const staticMap = createNullObject();
    const handlerData = [];
    const [regexp, indexReplacementMap, paramReplacementMap] = trie.buildRegExp();
    for (const r of [middleware, routes]) for (const path in r) {
      const handlers = r[path];
      const pathData = trie.paths[path];
      if (!pathData) {
        staticMap[path] = [handlers.map(([h]) => [h, createNullObject()]), emptyParam];
        continue;
      }
      handlerData[pathData[0]] = handlers.map(([h, handlerPath]) => [h, trie.paths[handlerPath][1].reduceRight((map, [key], i) => {
        map[key] = paramReplacementMap[pathData[1][i][1]];
        return map;
      }, createNullObject())]);
    }
    return [
      regexp,
      indexReplacementMap.map((i) => handlerData[i]),
      staticMap
    ];
  }
};

// node_modules/hono/dist/router/smart-router/router.js
var SmartRouter = class {
  static {
    __name(this, "SmartRouter");
  }
  name = "SmartRouter";
  #routers = [];
  #routes = [];
  constructor(init) {
    this.#routers = init.routers;
  }
  add(method, path, handler) {
    if (!this.#routes) throw new Error(MESSAGE_MATCHER_IS_ALREADY_BUILT);
    this.#routes.push([
      method,
      path,
      handler
    ]);
  }
  match(method, path) {
    if (!this.#routes) throw new Error("Fatal error");
    const routers = this.#routers;
    const routes = this.#routes;
    const len = routers.length;
    let i = 0;
    let res;
    for (; i < len; i++) {
      const router = routers[i];
      try {
        for (let i2 = 0, len2 = routes.length; i2 < len2; i2++) router.add(...routes[i2]);
        res = router.match(method, path);
      } catch (e) {
        if (e instanceof UnsupportedPathError) continue;
        throw e;
      }
      this.match = router.match.bind(router);
      this.#routers = [router];
      this.#routes = void 0;
      break;
    }
    if (i === len) throw new Error("Fatal error");
    this.name = `SmartRouter + ${this.activeRouter.name}`;
    return res;
  }
  get activeRouter() {
    if (this.#routes || this.#routers.length !== 1) throw new Error("No active router has been determined yet.");
    return this.#routers[0];
  }
};

// node_modules/hono/dist/router/trie-router/node.js
var emptyParams = createNullObject();
var order = 0;
var Node3 = class Node4 {
  static {
    __name(this, "Node");
  }
  #methods = [];
  #children = createNullObject();
  #patterns = [];
  #pattern;
  #params = emptyParams;
  insert(method, path, handler) {
    let curNode = this;
    const parts = splitRoutingPath(path);
    const possibleKeys = /* @__PURE__ */ new Set();
    let i = 0;
    for (const p of parts) {
      const nextP = parts[++i];
      const pattern = getPattern(p, nextP) || (nextP === void 0 && p && p.indexOf("*") === p.length - 1 ? p : null);
      const isParam = Array.isArray(pattern);
      const key = isParam ? pattern[0] : pattern || p;
      const child = curNode.#children[key] ||= new Node4();
      if (pattern && !child.#pattern) {
        child.#pattern = pattern;
        curNode.#patterns.push(child);
      }
      curNode = child;
      if (isParam) possibleKeys.add(pattern[1]);
    }
    curNode.#methods.push({ [method]: {
      handler,
      possibleKeys: [...possibleKeys],
      score: ++order
    } });
  }
  #pushHandlerSets(handlerSets, node, method, nodeParams, params) {
    for (let i = 0, len = node.#methods.length; i < len; i++) {
      const m = node.#methods[i];
      const handlerSet = m[method] || m["ALL"];
      if (handlerSet) {
        handlerSet.params = createNullObject();
        handlerSets.push(handlerSet);
        for (let i2 = 0, len2 = handlerSet.possibleKeys.length; i2 < len2; i2++) {
          const key = handlerSet.possibleKeys[i2];
          handlerSet.params[key] = params?.[key] && !i2 ? params[key] : nodeParams[key] ?? params?.[key];
        }
      }
    }
  }
  search(method, path) {
    const handlerSets = [];
    this.#params = emptyParams;
    let curNodes = [this];
    const parts = splitPath(path);
    const curNodesQueue = [];
    const len = parts.length;
    let partOffsets = null;
    for (let i = 0; i < len; i++) {
      const part = parts[i];
      const isLast = i === len - 1;
      const tempNodes = [];
      for (let j = 0, len2 = curNodes.length; j < len2; j++) {
        const node = curNodes[j];
        const nextNode = node.#children[part];
        if (nextNode) {
          nextNode.#params = node.#params;
          if (isLast) {
            if (nextNode.#children["*"]) this.#pushHandlerSets(handlerSets, nextNode.#children["*"], method, node.#params);
            this.#pushHandlerSets(handlerSets, nextNode, method, node.#params);
          } else tempNodes.push(nextNode);
        }
        for (const child of node.#patterns) {
          const pattern = child.#pattern;
          const params = node.#params === emptyParams ? {} : { ...node.#params };
          if (typeof pattern === "string") {
            if (pattern === "*" || part.startsWith(pattern.slice(0, -1))) {
              this.#pushHandlerSets(handlerSets, child, method, node.#params);
              if (pattern === "*") {
                child.#params = params;
                tempNodes.push(child);
              }
            }
            continue;
          }
          const [, name, matcher] = pattern;
          if (!part && matcher === true) continue;
          if (matcher !== true) {
            if (!partOffsets) {
              partOffsets = [];
              let offset = path[0] === "/" ? 1 : 0;
              for (let p = 0; p < len; p++) {
                partOffsets[p] = offset;
                offset += parts[p].length + 1;
              }
            }
            const restPathString = path.slice(partOffsets[i]);
            const m = matcher.exec(restPathString);
            if (m) {
              params[name] = m[0];
              this.#pushHandlerSets(handlerSets, child, method, node.#params, params);
              if (m[0].length === restPathString.length && child.#children["*"]) this.#pushHandlerSets(handlerSets, child.#children["*"], method, node.#params, params);
              for (const _ in child.#children) {
                child.#params = params;
                const componentCount = m[0].match(/\//g)?.length ?? 0;
                (curNodesQueue[componentCount] ||= []).push(child);
                break;
              }
              continue;
            }
          }
          if (matcher === true || matcher.test(part)) {
            params[name] = part;
            if (isLast) {
              this.#pushHandlerSets(handlerSets, child, method, params, node.#params);
              if (child.#children["*"]) this.#pushHandlerSets(handlerSets, child.#children["*"], method, params, node.#params);
            } else {
              child.#params = params;
              tempNodes.push(child);
            }
          }
        }
      }
      const shifted = curNodesQueue.shift();
      curNodes = shifted ? tempNodes.concat(shifted) : tempNodes;
    }
    if (handlerSets[1]) handlerSets.sort((a, b) => {
      return a.score - b.score;
    });
    return [handlerSets.map(({ handler, params }) => [handler, params])];
  }
};

// node_modules/hono/dist/router/trie-router/router.js
var TrieRouter = class {
  static {
    __name(this, "TrieRouter");
  }
  name = "TrieRouter";
  #node = new Node3();
  add(method, path, handler) {
    for (const result of checkOptionalParameter(path) || [path]) this.#node.insert(method, result, handler);
  }
  match(method, path) {
    return this.#node.search(method, path);
  }
};

// node_modules/hono/dist/hono.js
var Hono3 = class extends Hono {
  static {
    __name(this, "Hono");
  }
  /**
  * Creates an instance of the Hono class.
  *
  * @param options - Optional configuration options for the Hono instance.
  */
  constructor(options = {}) {
    super(options);
    this.router = options.router ?? new SmartRouter({ routers: [new RegExpRouter(), new TrieRouter()] });
  }
};

// node_modules/hono/dist/middleware/cors/index.js
var cors = /* @__PURE__ */ __name((options) => {
  const opts = {
    origin: "*",
    allowMethods: [
      "GET",
      "HEAD",
      "PUT",
      "POST",
      "DELETE",
      "PATCH",
      "QUERY"
    ],
    allowHeaders: [],
    exposeHeaders: [],
    ...options
  };
  const exposeHeadersStr = opts.exposeHeaders?.length ? opts.exposeHeaders.join(",") : void 0;
  const allowHeadersStr = opts.allowHeaders?.length ? opts.allowHeaders.join(",") : void 0;
  const findAllowOrigin = ((optsOrigin) => {
    if (typeof optsOrigin === "string") {
      if (optsOrigin === "*") return () => optsOrigin;
      else return (origin) => optsOrigin === origin ? origin : null;
    } else if (typeof optsOrigin === "function") return optsOrigin;
    else return (origin) => optsOrigin.includes(origin) ? origin : null;
  })(opts.origin);
  const findAllowMethods = ((optsAllowMethods) => {
    if (typeof optsAllowMethods === "function") return async (origin, c) => (await optsAllowMethods(origin, c)).join(",");
    else if (Array.isArray(optsAllowMethods)) {
      const methodsStr = optsAllowMethods.join(",");
      return () => methodsStr;
    } else return () => "";
  })(opts.allowMethods);
  return /* @__PURE__ */ __name(async function cors2(c, next) {
    function set(key, value) {
      c.res.headers.set(key, value);
    }
    __name(set, "set");
    const allowOrigin = await findAllowOrigin(c.req.header("origin") || "", c);
    if (allowOrigin) set("Access-Control-Allow-Origin", allowOrigin);
    if (opts.credentials) set("Access-Control-Allow-Credentials", "true");
    if (exposeHeadersStr) set("Access-Control-Expose-Headers", exposeHeadersStr);
    if (c.req.method === "OPTIONS") {
      if (opts.origin !== "*") c.res.headers.append("Vary", "Origin");
      if (opts.maxAge != null) set("Access-Control-Max-Age", opts.maxAge.toString());
      const allowMethods = await findAllowMethods(c.req.header("origin") || "", c);
      if (allowMethods) set("Access-Control-Allow-Methods", allowMethods);
      let headersStr = allowHeadersStr;
      if (!headersStr) {
        const requestHeaders = c.req.header("Access-Control-Request-Headers");
        if (requestHeaders) headersStr = requestHeaders.split(",").map((h) => h.trim()).join(",");
      }
      if (headersStr) {
        set("Access-Control-Allow-Headers", headersStr);
        c.res.headers.append("Vary", "Access-Control-Request-Headers");
      }
      c.res.headers.delete("Content-Length");
      c.res.headers.delete("Content-Type");
      return new Response(null, {
        headers: c.res.headers,
        status: 204,
        statusText: "No Content"
      });
    }
    await next();
    if (opts.origin !== "*") c.header("Vary", "Origin", { append: true });
  }, "cors");
}, "cors");

// src/os_relay.ts
var OS_CAP = "os1";
var OS_MAX_AUTHED = 8;
var OS_MAX_PREAUTH = 4;
var OS_MAX_PREAUTH_PER_IP = 2;
var OS_MAX_INBOUND_BYTES = 1024 * 1024;
var KEEPALIVE = '{"t":"ka"}';
var CLOSE = {
  /** The brain's uplink is not connected: the computer is off, asleep, or offline. */
  OFFLINE: 4503,
  /** The relay was retired (the deck key rotated) — this pairing points nowhere now. */
  RETIRED: 4404,
  /** The brain is connected but predates the OS channel. */
  UPDATE_NAVIG: 4426,
  /** The brain is up but NAVIG OS is not running on the PC. */
  OS_NOT_RUNNING: 4410,
  /** Unknown or revoked device. */
  DEVICE: 4401,
  /** A cap: sessions, pending connections, budget, or a client too slow to keep up. */
  LIMIT: 4429,
  /** The uplink restarted; transient, the client reconnects. */
  UPLINK_RESTART: 1012,
  TOO_BIG: 1009,
  UNSUPPORTED: 1003
};
function socketRole(tags) {
  return tags.includes("os") ? "os" : "uplink";
}
__name(socketRole, "socketRole");
var SID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
function sidTag(sid) {
  return `sid:${sid}`;
}
__name(sidTag, "sidTag");
function sidFromTags(tags) {
  for (const t of tags) {
    if (t.startsWith("sid:")) {
      const sid = t.slice(4);
      return SID_RE.test(sid) ? sid : null;
    }
  }
  return null;
}
__name(sidFromTags, "sidFromTags");
function encodeData(sid, payload) {
  return `@${sid}:${payload}`;
}
__name(encodeData, "encodeData");
function parseDataFrame(message) {
  if (!message.startsWith("@")) return null;
  const colon = message.indexOf(":");
  if (colon !== 37) return null;
  const sid = message.slice(1, colon);
  if (!SID_RE.test(sid)) return null;
  return { sid, payload: message.slice(colon + 1) };
}
__name(parseDataFrame, "parseDataFrame");
function decideOpen(s) {
  if (s.retired) return { accept: false, code: CLOSE.RETIRED, reason: "relay moved: pair again" };
  if (!s.uplinkOpen) return { accept: false, code: CLOSE.OFFLINE, reason: "computer offline" };
  if (!s.uplinkCaps.includes(OS_CAP)) {
    return { accept: false, code: CLOSE.UPDATE_NAVIG, reason: "update NAVIG on your computer" };
  }
  if (s.authed >= OS_MAX_AUTHED) return { accept: false, code: CLOSE.LIMIT, reason: "too many sessions" };
  if (s.preauth >= OS_MAX_PREAUTH || s.preauthFromIp >= OS_MAX_PREAUTH_PER_IP) {
    return { accept: false, code: CLOSE.LIMIT, reason: "too many pending connections" };
  }
  return { accept: true };
}
__name(decideOpen, "decideOpen");
function decidePromote(authedOthers) {
  if (authedOthers >= OS_MAX_AUTHED) return { accept: false, code: CLOSE.LIMIT, reason: "too many sessions" };
  return { accept: true };
}
__name(decidePromote, "decidePromote");
function inboundTooBig(message) {
  if (message.length > OS_MAX_INBOUND_BYTES) return true;
  if (message.length * 3 <= OS_MAX_INBOUND_BYTES) return false;
  return new TextEncoder().encode(message).byteLength > OS_MAX_INBOUND_BYTES;
}
__name(inboundTooBig, "inboundTooBig");
function sendableCloseCode(code) {
  const n = typeof code === "number" && Number.isInteger(code) ? code : 1e3;
  if (n === 1e3 || n >= 3e3 && n <= 4999) return n;
  if (n >= 1001 && n <= 1014 && n !== 1004 && n !== 1005 && n !== 1006) return n;
  return 4e3;
}
__name(sendableCloseCode, "sendableCloseCode");
function sendableCloseReason(reason) {
  const text = typeof reason === "string" ? reason : "";
  const bytes = new TextEncoder().encode(text);
  if (bytes.length <= 123) return text;
  let cut = text.slice(0, 120);
  while (new TextEncoder().encode(cut).length > 120) cut = cut.slice(0, -1);
  return cut;
}
__name(sendableCloseReason, "sendableCloseReason");

// src/protocol.ts
var DECK_PREFIXES = ["/api/", "/runtime/", "/mesh/"];
function offlineSnapshotAllowed(pathname) {
  return DECK_PREFIXES.some((p) => pathname.startsWith(p));
}
__name(offlineSnapshotAllowed, "offlineSnapshotAllowed");

// src/brain_socket.ts
var REQUEST_TIMEOUT_MS = 3e4;
var MAX_QUEUE = 500;
var QUEUE_TTL_MS = 24 * 60 * 60 * 1e3;
var DEFAULT_RATE_LIMIT_PER_MIN = 600;
var RATE_BURST = 200;
var BrainSocket = class {
  static {
    __name(this, "BrainSocket");
  }
  state;
  env;
  sql;
  pending = /* @__PURE__ */ new Map();
  sse = /* @__PURE__ */ new Set();
  heartbeats = /* @__PURE__ */ new Map();
  encoder = new TextEncoder();
  // Token bucket for the per-tenant rate limit (see constants above).
  rateTokens = RATE_BURST;
  rateRefillAt = 0;
  constructor(state, env2) {
    this.state = state;
    this.env = env2;
    this.sql = state.storage.sql;
    this.sql.exec(
      `CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
       CREATE TABLE IF NOT EXISTS queue (
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         kind TEXT NOT NULL,
         path TEXT NOT NULL,
         headers TEXT NOT NULL,
         body TEXT NOT NULL,
         created INTEGER NOT NULL,
         req_id TEXT
       );`
    );
    try {
      this.sql.exec("ALTER TABLE queue ADD COLUMN req_id TEXT");
    } catch {
    }
    this.state.setWebSocketAutoResponse(new WebSocketRequestResponsePair(KEEPALIVE, KEEPALIVE));
  }
  // ── HTTP surface (called by the Worker via the DO stub) ────────────────────
  async fetch(request) {
    const url = new URL(request.url);
    if (url.pathname === "/__uplink") {
      if (request.headers.get("Upgrade") !== "websocket") {
        return new Response("expected websocket", { status: 426 });
      }
      const pair = new WebSocketPair();
      const [client, server] = [pair[0], pair[1]];
      for (const prev of this.state.getWebSockets()) {
        try {
          prev.close(CLOSE.UPLINK_RESTART, "superseded");
        } catch {
        }
      }
      this.state.acceptWebSocket(server, ["uplink"]);
      server.serializeAttachment({ caps: [] });
      return new Response(null, { status: 101, webSocket: client });
    }
    if (url.pathname === "/__os") {
      return this.openOs(request);
    }
    if (url.pathname === "/__events") {
      return this.openSse();
    }
    const kind = url.pathname.startsWith("/tg/") ? "telegram" : url.pathname.startsWith("/sms/") ? "sms" : "deck";
    if (this.rateLimited()) {
      return new Response(JSON.stringify({ error: "rate_limited" }), {
        status: 429,
        headers: { "content-type": "application/json", "retry-after": "1" }
      });
    }
    return this.forward(request, kind);
  }
  /** True when this tenant has exceeded its inbound request budget. */
  rateLimited() {
    const perMin = Number(this.env.RATE_LIMIT_PER_MIN ?? DEFAULT_RATE_LIMIT_PER_MIN);
    if (!Number.isFinite(perMin) || perMin <= 0) return false;
    const now = Date.now();
    if (this.rateRefillAt === 0) this.rateRefillAt = now;
    const elapsed = now - this.rateRefillAt;
    if (elapsed > 0) {
      this.rateTokens = Math.min(RATE_BURST, this.rateTokens + elapsed / 6e4 * perMin);
      this.rateRefillAt = now;
    }
    if (this.rateTokens < 1) return true;
    this.rateTokens -= 1;
    return false;
  }
  // ── WebSocket Hibernation handlers ─────────────────────────────────────────
  async webSocketMessage(ws, message) {
    if (socketRole(this.state.getTags(ws)) === "os") {
      this.fromBrowser(ws, message);
      return;
    }
    if (typeof message === "string") {
      const data = parseDataFrame(message);
      if (data) {
        this.toBrowser(data.sid, data.payload);
        return;
      }
    }
    let frame;
    try {
      frame = JSON.parse(typeof message === "string" ? message : new TextDecoder().decode(message));
    } catch {
      return;
    }
    switch (frame.t) {
      case "res": {
        this.sql.exec("DELETE FROM queue WHERE req_id = ?", frame.id);
        const p = this.pending.get(frame.id);
        if (p) {
          clearTimeout(p.timer);
          this.pending.delete(frame.id);
          p.resolve(
            new Response(frame.body, {
              status: frame.status,
              headers: frame.headers
            })
          );
        }
        return;
      }
      case "event":
        this.fanout(frame.event, frame.data);
        return;
      case "snapshot":
        this.sql.exec(
          "INSERT INTO kv (k, v) VALUES ('snapshot', ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v",
          frame.data
        );
        this.sql.exec(
          "INSERT INTO kv (k, v) VALUES ('snapshot_at', ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v",
          String(Date.now())
        );
        return;
      case "hello":
        if (frame.snapshot) {
          this.sql.exec(
            "INSERT INTO kv (k, v) VALUES ('snapshot', ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v",
            frame.snapshot
          );
        }
        ws.serializeAttachment({
          caps: Array.isArray(frame.caps) ? frame.caps.filter((c) => typeof c === "string").slice(0, 16) : []
        });
        await this.replayQueue(ws);
        return;
      case "pong":
        return;
      case "os_ok": {
        const sock = this.osSocket(frame.sid);
        const att = sock?.deserializeAttachment();
        if (!sock || !att) return;
        let authedOthers = 0;
        for (const other of this.state.getWebSockets("os")) {
          if (other !== sock && other.deserializeAttachment()?.authed) authedOthers++;
        }
        const decision = decidePromote(authedOthers);
        if (!decision.accept) {
          this.closeQuietly(sock, decision.code, decision.reason);
          this.tellBrainClosed(sock, decision.code, decision.reason);
          return;
        }
        sock.serializeAttachment({ ...att, authed: true });
        return;
      }
      case "os_close": {
        const sock = this.osSocket(frame.sid);
        if (sock) this.closeQuietly(sock, frame.code, frame.reason ?? "");
        return;
      }
      case "retire":
        this.sql.exec(
          "INSERT INTO kv (k, v) VALUES ('retired', '1') ON CONFLICT(k) DO UPDATE SET v = excluded.v"
        );
        this.closeAllOs(CLOSE.RETIRED, "relay moved: pair again");
        return;
    }
  }
  async webSocketClose(ws, code, reason) {
    try {
      ws.close();
    } catch {
    }
    if (socketRole(this.state.getTags(ws)) === "os") {
      this.tellBrainClosed(ws, code, reason);
      return;
    }
    this.failAllPending();
    this.closeAllOs(CLOSE.UPLINK_RESTART, "computer reconnecting");
  }
  async webSocketError(ws) {
    if (socketRole(this.state.getTags(ws)) === "os") {
      this.tellBrainClosed(ws, 1011, "browser socket error");
      return;
    }
    this.failAllPending();
    this.closeAllOs(CLOSE.UPLINK_RESTART, "computer reconnecting");
  }
  // ── NAVIG OS channel ───────────────────────────────────────────────────────
  openOs(request) {
    if (request.headers.get("Upgrade") !== "websocket") {
      return new Response("expected websocket", { status: 426 });
    }
    const ip = request.headers.get("cf-connecting-ip") ?? "";
    const uplink = this.liveSocket();
    const caps = uplink?.deserializeAttachment()?.caps ?? [];
    let authed = 0;
    let preauth = 0;
    let preauthFromIp = 0;
    for (const sock of this.state.getWebSockets("os")) {
      const att = sock.deserializeAttachment();
      if (att?.authed) authed++;
      else {
        preauth++;
        if (ip && att?.ip === ip) preauthFromIp++;
      }
    }
    const retired = this.sql.exec("SELECT v FROM kv WHERE k = 'retired'").toArray().length > 0;
    let decision = decideOpen({ retired, uplinkOpen: !!uplink, uplinkCaps: caps, authed, preauth, preauthFromIp });
    if (decision.accept && this.rateLimited()) {
      decision = { accept: false, code: CLOSE.LIMIT, reason: "rate limited" };
    }
    const pair = new WebSocketPair();
    const [client, server] = [pair[0], pair[1]];
    if (!decision.accept || !uplink) {
      server.accept();
      const code = decision.accept ? CLOSE.OFFLINE : decision.code;
      const reason = decision.accept ? "computer offline" : decision.reason;
      server.close(code, reason);
      return new Response(null, { status: 101, webSocket: client });
    }
    const sid = crypto.randomUUID();
    this.state.acceptWebSocket(server, ["os", sidTag(sid)]);
    server.serializeAttachment({ authed: false, ip });
    try {
      uplink.send(
        JSON.stringify({
          t: "os_open",
          sid,
          ua: (request.headers.get("user-agent") ?? "").slice(0, 200),
          ip,
          country: request.headers.get("cf-ipcountry") ?? ""
        })
      );
    } catch {
      server.close(CLOSE.OFFLINE, "computer offline");
    }
    return new Response(null, { status: 101, webSocket: client });
  }
  /** A browser message: hand it to the brain verbatim under its stream id. */
  fromBrowser(ws, message) {
    const sid = sidFromTags(this.state.getTags(ws));
    if (!sid) return this.closeQuietly(ws, 1011, "untracked socket");
    if (typeof message !== "string") {
      return this.closeQuietly(ws, CLOSE.UNSUPPORTED, "text frames only");
    }
    if (inboundTooBig(message)) {
      this.closeQuietly(ws, CLOSE.TOO_BIG, "message too big");
      this.tellBrainClosed(ws, CLOSE.TOO_BIG, "message too big");
      return;
    }
    const uplink = this.liveSocket();
    if (!uplink) return this.closeQuietly(ws, CLOSE.OFFLINE, "computer offline");
    try {
      uplink.send(encodeData(sid, message));
    } catch {
      this.closeQuietly(ws, CLOSE.OFFLINE, "computer offline");
    }
  }
  /** A brain message for one stream: deliver it as-is (sends from a DO are not billed). */
  toBrowser(sid, payload) {
    const sock = this.osSocket(sid);
    if (!sock) {
      try {
        this.liveSocket()?.send(JSON.stringify({ t: "os_close", sid, code: 1001, reason: "browser gone" }));
      } catch {
      }
      return;
    }
    try {
      sock.send(payload);
    } catch {
      this.tellBrainClosed(sock, 1011, "send to browser failed");
    }
  }
  osSocket(sid) {
    for (const sock of this.state.getWebSockets(sidTag(sid))) {
      if (sock.readyState === WebSocket.READY_STATE_OPEN) return sock;
    }
    return null;
  }
  tellBrainClosed(ws, code, reason) {
    const sid = sidFromTags(this.state.getTags(ws));
    const uplink = this.liveSocket();
    if (!sid || !uplink) return;
    try {
      uplink.send(JSON.stringify({ t: "os_close", sid, code, reason: sendableCloseReason(reason) }));
    } catch {
    }
  }
  closeAllOs(code, reason) {
    for (const sock of this.state.getWebSockets("os")) this.closeQuietly(sock, code, reason);
  }
  closeQuietly(ws, code, reason) {
    try {
      ws.close(sendableCloseCode(code), sendableCloseReason(reason));
    } catch {
    }
  }
  // ── Forwarding ─────────────────────────────────────────────────────────────
  /** The brain's uplink — never a browser socket, whatever order they were accepted in. */
  liveSocket() {
    for (const ws of this.state.getWebSockets()) {
      if (socketRole(this.state.getTags(ws)) !== "uplink") continue;
      if (ws.readyState === WebSocket.READY_STATE_OPEN) return ws;
    }
    return null;
  }
  async forward(request, kind) {
    const ws = this.liveSocket();
    const url = new URL(request.url);
    url.searchParams.delete("key");
    const path = url.pathname + url.search;
    const headers = {};
    request.headers.forEach((v, k) => {
      if (k === "host" || k === "connection" || k === "upgrade") return;
      headers[k] = v;
    });
    const body = request.method === "GET" || request.method === "HEAD" ? "" : await request.text();
    if (!ws) {
      if (kind === "deck") return this.offlineResponse(offlineSnapshotAllowed(url.pathname));
      this.enqueue(kind, path, headers, body, kind === "telegram" ? crypto.randomUUID() : null);
      return new Response(JSON.stringify({ queued: true }), {
        status: 202,
        headers: { "content-type": "application/json" }
      });
    }
    const id = crypto.randomUUID();
    const frame = { t: "req", id, kind, method: request.method, path, headers, body };
    if (kind === "telegram") {
      this.enqueue(kind, path, headers, body, id);
      try {
        ws.send(JSON.stringify(frame));
      } catch {
        this.sql.exec("DELETE FROM queue WHERE req_id = ?", id);
        return new Response(JSON.stringify({ error: "uplink_send_failed" }), {
          status: 502,
          headers: { "content-type": "application/json" }
        });
      }
      return new Response(JSON.stringify({ ok: true, forwarded: true }), {
        status: 200,
        headers: { "content-type": "application/json" }
      });
    }
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        resolve(new Response(JSON.stringify({ error: "brain_timeout" }), { status: 504 }));
      }, REQUEST_TIMEOUT_MS);
      this.pending.set(id, { resolve, timer });
      try {
        ws.send(JSON.stringify(frame));
      } catch {
        clearTimeout(timer);
        this.pending.delete(id);
        resolve(new Response(JSON.stringify({ error: "uplink_send_failed" }), { status: 502 }));
      }
    });
  }
  failAllPending() {
    for (const [, p] of this.pending) {
      clearTimeout(p.timer);
      p.resolve(new Response(JSON.stringify({ error: "brain_disconnected" }), { status: 502 }));
    }
    this.pending.clear();
  }
  /** `withSnapshot` is false for tenant-path routes, whose callers hold no key. */
  offlineResponse(withSnapshot = true) {
    const snap = withSnapshot ? this.sql.exec("SELECT v FROM kv WHERE k = 'snapshot'").toArray()[0]?.v : void 0;
    const at = withSnapshot ? this.sql.exec("SELECT v FROM kv WHERE k = 'snapshot_at'").toArray()[0]?.v : void 0;
    return new Response(
      JSON.stringify({
        offline: true,
        since: at ? Number(at) : null,
        snapshot: snap ? JSON.parse(snap) : null
      }),
      {
        // 503 so the deck's existing "unreachable" path keeps the last-known
        // UI instead of rendering the offline envelope as real data; the
        // header + body let it show a "brain offline" banner + cached snapshot.
        status: 503,
        headers: {
          "content-type": "application/json",
          "x-navig-brain": "offline",
          "retry-after": "10"
        }
      }
    );
  }
  // ── Offline queue ────────────────────────────────────────────────────────────
  /** `reqId` set = the row is kept until the brain's `res` for that id (replayed until then);
   *  null = fire-and-forget, dropped once replayed. */
  enqueue(kind, path, headers, body, reqId) {
    this.sql.exec(
      "INSERT INTO queue (kind, path, headers, body, created, req_id) VALUES (?, ?, ?, ?, ?, ?)",
      kind,
      path,
      JSON.stringify(headers),
      body,
      Date.now(),
      reqId
    );
    this.sql.exec(
      `DELETE FROM queue WHERE id IN (
         SELECT id FROM queue ORDER BY id DESC LIMIT -1 OFFSET ?
       )`,
      MAX_QUEUE
    );
  }
  async replayQueue(ws) {
    const cutoff = Date.now() - QUEUE_TTL_MS;
    this.sql.exec("DELETE FROM queue WHERE created < ?", cutoff);
    const rows = this.sql.exec("SELECT * FROM queue ORDER BY id ASC").toArray();
    for (const row of rows) {
      const frame = {
        t: "req",
        // The caller is long gone; a row with a req_id keeps it so the brain's `res`
        // clears the row, anything else is fire-and-forget.
        id: row.req_id ?? crypto.randomUUID(),
        kind: row.kind,
        method: "POST",
        path: row.path,
        headers: JSON.parse(row.headers),
        body: row.body
      };
      try {
        ws.send(JSON.stringify(frame));
      } catch {
        return;
      }
    }
    this.sql.exec("DELETE FROM queue WHERE req_id IS NULL");
  }
  // ── SSE fanout ───────────────────────────────────────────────────────────────
  openSse() {
    const { readable, writable } = new TransformStream();
    const writer = writable.getWriter();
    this.sse.add(writer);
    const live = this.liveSocket() ? "online" : "offline";
    writer.write(this.encoder.encode(`: navig-lighthouse
event: hello
data: {"brain":"${live}"}

`)).catch(() => this.dropSse(writer));
    const beat = setInterval(() => {
      writer.write(this.encoder.encode(": heartbeat\n\n")).catch(() => {
        clearInterval(beat);
        this.dropSse(writer);
      });
    }, 2e4);
    this.heartbeats.set(writer, beat);
    return new Response(readable, {
      headers: {
        "content-type": "text/event-stream",
        "cache-control": "no-cache",
        connection: "keep-alive"
      }
    });
  }
  dropSse(writer) {
    const beat = this.heartbeats.get(writer);
    if (beat !== void 0) {
      clearInterval(beat);
      this.heartbeats.delete(writer);
    }
    this.sse.delete(writer);
  }
  fanout(event, data) {
    const chunk = this.encoder.encode(`event: ${event}
data: ${data}

`);
    for (const writer of this.sse) {
      writer.write(chunk).catch(() => this.dropSse(writer));
    }
  }
};

// src/ownership_core.ts
var SINGLETON_ROLES = ["brain", "all-in-one"];
var DEFAULT_TTL_SECONDS = 300;
var MAX_TAKEOVERS = 50;
var SCHEMA = 1;
function nowSeconds() {
  return Date.now() / 1e3;
}
__name(nowSeconds, "nowSeconds");
function asNumber(value, fallback = 0) {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : fallback;
}
__name(asNumber, "asNumber");
function asText(value) {
  return typeof value === "string" ? value.trim() : value == null ? "" : String(value).trim();
}
__name(asText, "asText");
function normalizeRole(value) {
  const text = asText(value).toLowerCase().replace(/_/g, "-").replace(/ /g, "-");
  if (!text) return "all-in-one";
  const aliases = {
    server: "brain",
    "always-on": "brain",
    allinone: "all-in-one",
    local: "all-in-one",
    node: "client",
    worker: "executor"
  };
  return aliases[text] || text;
}
__name(normalizeRole, "normalizeRole");
function isSingletonRole(role) {
  return SINGLETON_ROLES.indexOf(normalizeRole(role)) !== -1;
}
__name(isSingletonRole, "isSingletonRole");
function parseTakeover(raw2) {
  if (typeof raw2 !== "object" || raw2 === null) return null;
  const item = raw2;
  return {
    at: asNumber(item.at),
    op: asText(item.op) || "takeover",
    from_owner: asText(item.from_owner),
    from_hostname: asText(item.from_hostname),
    from_epoch: asNumber(item.from_epoch),
    to_owner: asText(item.to_owner),
    to_hostname: asText(item.to_hostname),
    to_epoch: asNumber(item.to_epoch),
    reason: asText(item.reason),
    forced: Boolean(item.forced),
    expired: Boolean(item.expired),
    request_id: asText(item.request_id) || void 0
  };
}
__name(parseTakeover, "parseTakeover");
function parseMoved(raw2) {
  if (typeof raw2 !== "object" || raw2 === null) return null;
  const item = raw2;
  const to = asText(item.to);
  if (!to) return null;
  return {
    to,
    migration: asText(item.migration),
    reason: asText(item.reason),
    at: asNumber(item.at),
    from_owner: asText(item.from_owner)
  };
}
__name(parseMoved, "parseMoved");
function recordFromJson(raw2) {
  if (typeof raw2 !== "object" || raw2 === null) return null;
  const data = raw2;
  if (!asText(data.owner_id)) return null;
  const takeovers = Array.isArray(data.takeovers) ? data.takeovers.map(parseTakeover).filter((t) => t !== null) : [];
  return {
    schema: asNumber(data.schema, SCHEMA) || SCHEMA,
    owner_id: asText(data.owner_id),
    role: normalizeRole(data.role),
    epoch: Math.max(1, Math.trunc(asNumber(data.epoch, 1))),
    ttl: Math.max(30, Math.trunc(asNumber(data.ttl, DEFAULT_TTL_SECONDS))),
    acquired_at: asNumber(data.acquired_at),
    heartbeat_at: asNumber(data.heartbeat_at),
    updated_at: asNumber(data.updated_at),
    hostname: asText(data.hostname),
    reason: asText(data.reason),
    takeovers,
    moved_marker: parseMoved(data.moved_marker),
    released_at: data.released_at == null ? null : asNumber(data.released_at)
  };
}
__name(recordFromJson, "recordFromJson");
function emptyRecord(now, ownerId, role, ttl, reason, hostname) {
  return {
    schema: SCHEMA,
    owner_id: ownerId,
    role,
    epoch: 1,
    ttl,
    acquired_at: now,
    heartbeat_at: now,
    updated_at: now,
    hostname,
    reason,
    takeovers: [],
    moved_marker: null,
    released_at: null
  };
}
__name(emptyRecord, "emptyRecord");
function isReleased(record) {
  return record === null || record.released_at !== null || !record.owner_id;
}
__name(isReleased, "isReleased");
function isExpired(record, now) {
  if (isReleased(record)) return true;
  return now > record.heartbeat_at + record.ttl;
}
__name(isExpired, "isExpired");
function isLive(record, now) {
  return !isReleased(record) && !isExpired(record, now);
}
__name(isLive, "isLive");
function expiresIn(record, now) {
  if (isReleased(record)) return 0;
  const left = record.heartbeat_at + record.ttl - now;
  return Math.max(0, Math.trunc(left));
}
__name(expiresIn, "expiresIn");
function statusOf(record, viewer, now, viewerRole = "all-in-one") {
  const moved = record ? record.moved_marker : null;
  let state;
  if (record === null) state = "free";
  else if (record.released_at !== null) state = moved ? "moved" : "released";
  else if (isExpired(record, now)) state = "expired";
  else state = "held";
  const ownerId = record ? record.owner_id : "";
  const ours = Boolean(record) && record.owner_id === viewer && record.released_at === null;
  const canClaim = isSingletonRole(viewerRole) && (record === null || record.released_at !== null && !(moved && moved.from_owner === viewer) || ours);
  const canTakeover = Boolean(record) && record.released_at === null && record.owner_id !== viewer && !isLive(record, now);
  const summary = record === null ? "free \u2014 nobody owns this Brain" : `${record.hostname || "unknown-host"} (${record.owner_id}), role ${record.role}, epoch ${record.epoch}, ${state}, ${expiresIn(record, now)}s of TTL left`;
  return {
    state,
    owner_id: ownerId,
    owner_hostname: record ? record.hostname : "",
    owner_role: record ? record.role : "",
    epoch: record ? record.epoch : 0,
    acquired_at: record ? record.acquired_at : 0,
    heartbeat_at: record ? record.heartbeat_at : 0,
    expires_in_s: expiresIn(record, now),
    released_at: record ? record.released_at : null,
    moved_marker: moved,
    is_owner: ours,
    can_claim: canClaim,
    can_takeover: canTakeover,
    summary
  };
}
__name(statusOf, "statusOf");
function ok(body) {
  return { status: 200, body: { ok: true, ...body } };
}
__name(ok, "ok");
function fail(status, code, error, extra) {
  return { status, body: { ok: false, code, error, ...extra || {} } };
}
__name(fail, "fail");
function auditEntry(op, from, toEpoch, actor, role, hostname, reason, now, requestId, forced, expired) {
  return {
    at: now,
    op,
    from_owner: from ? from.owner_id : "",
    from_hostname: from ? from.hostname : "",
    from_epoch: from ? from.epoch : 0,
    to_owner: actor,
    to_hostname: hostname,
    to_epoch: toEpoch,
    reason,
    forced,
    expired,
    request_id: requestId || void 0
  };
}
__name(auditEntry, "auditEntry");
var OwnershipEngine = class {
  static {
    __name(this, "OwnershipEngine");
  }
  store;
  constructor(store) {
    this.store = store;
  }
  handle(request) {
    const req = request && typeof request === "object" ? request : {};
    const op = asText(req.op) || "status";
    if (op === "status") return this.status(req);
    if (["acquire", "renew", "release", "takeover", "mark_moved", "clear_moved"].indexOf(op) === -1) {
      return fail(400, "unknown_op", `unknown ownership op ${JSON.stringify(op)}; known: status, acquire, renew, release, takeover, mark_moved, clear_moved`);
    }
    const requestId = asText(req.request_id);
    if (requestId) {
      const replay = this.store.loadIdempotent(requestId);
      if (replay) return { status: 200, body: { ...replay, idempotent: true } };
    }
    const result = this.mutate(op, req);
    if (requestId && result.status === 200) this.store.saveIdempotent(requestId, result.body);
    return result;
  }
  status(req) {
    const now = req.now ?? nowSeconds();
    const record = this.store.loadRecord();
    const viewer = asText(req.owner_id);
    return ok({
      authority: "lighthouse-ownership",
      server_time: now,
      record,
      status: statusOf(record, viewer, now, normalizeRole(req.role))
    });
  }
  mutate(op, req) {
    const now = req.now ?? nowSeconds();
    const ownerId = asText(req.owner_id);
    const role = normalizeRole(req.role);
    const hostname = asText(req.hostname);
    const requestId = asText(req.request_id);
    const reason = asText(req.reason);
    if (!ownerId) return fail(400, "owner_id_required", "every mutation must name the machine (owner_id) it is for");
    if (!isSingletonRole(role)) {
      return fail(403, "role_forbidden", `this install is a ${role} \u2014 a ${role} may read ownership status but can never own the Brain or change its owner. A Brain role (brain / all-in-one) must do it.`);
    }
    const current = this.store.loadRecord();
    const ttl = Math.max(30, Math.trunc(asNumber(req.ttl ?? (current ? current.ttl : DEFAULT_TTL_SECONDS), DEFAULT_TTL_SECONDS)));
    if (op === "acquire") return this.acquire(req, current, ownerId, role, ttl, hostname, reason, now, requestId);
    if (op === "renew") return this.renew(req, current, ownerId, role, ttl, hostname, now);
    if (op === "release") return this.release(req, current, ownerId, role, hostname, reason, now, requestId);
    if (op === "takeover") return this.takeover(req, current, ownerId, role, ttl, hostname, reason, now, requestId);
    if (op === "mark_moved") return this.markMoved(req, current, ownerId, hostname, reason, now, requestId);
    return this.clearMoved(req, current, ownerId, hostname, reason, now, requestId);
  }
  acquire(req, current, ownerId, role, ttl, hostname, reason, now, requestId) {
    if (current === null) {
      const record = emptyRecord(now, ownerId, role, ttl, reason || "first claim", hostname);
      this.save(record, auditEntry("acquire", null, 1, ownerId, role, hostname, record.reason, now, requestId, false, false));
      return ok({ record, epoch: record.epoch });
    }
    const moved = current.moved_marker;
    if (moved && moved.from_owner === ownerId) {
      return fail(409, "moved", `this Brain moved to ${moved.to}${moved.migration ? ` (migration ${moved.migration})` : ""}. The machine it left must not re-claim it \u2014 that is the fence the migration wrote. Roll the migration back explicitly if it truly has to come back.`, { record: current });
    }
    if (current.released_at !== null || !current.owner_id) {
      const record = {
        ...current,
        owner_id: ownerId,
        role,
        epoch: current.epoch + 1,
        ttl,
        acquired_at: now,
        heartbeat_at: now,
        updated_at: now,
        hostname,
        reason: reason || "claimed a released record",
        released_at: null,
        // Arriving on the machine a move was recorded for is what completes a
        // migration; the marker is cleared and the arrival is audited.
        moved_marker: moved ? null : current.moved_marker
      };
      this.save(record, auditEntry("acquire", current, record.epoch, ownerId, role, hostname, record.reason, now, requestId, false, true));
      return ok({ record, epoch: record.epoch, cleared_moved: moved !== null });
    }
    if (current.owner_id === ownerId) {
      const record = { ...current, heartbeat_at: now, updated_at: now, ttl, role, hostname: hostname || current.hostname, reason: reason || current.reason };
      this.store.saveRecord(record);
      return ok({ record, epoch: record.epoch, renewed: true });
    }
    if (isLive(current, now)) {
      return fail(
        409,
        "held",
        `the Brain is owned by ${current.hostname || "another machine"} (${current.owner_id}), epoch ${current.epoch}, heartbeating (${expiresIn(current, now)}s of TTL left). Two Brains on one deployment is the bug this authority exists to prevent. If that machine is gone: navig brain takeover --from ${current.owner_id} --reason <why>`,
        { record: current }
      );
    }
    if (req.auto_promote === true) {
      return this.takeover(req, current, ownerId, role, ttl, hostname, reason || `auto-promoted: the authority's record expired ${Math.trunc(now - current.heartbeat_at)}s ago`, now, requestId, false);
    }
    return fail(
      409,
      "expired",
      `the Brain record held by ${current.hostname || "another machine"} (${current.owner_id}) has EXPIRED (last heartbeat ${Math.trunc(now - current.heartbeat_at)}s ago). Automatic promotion is off (auto_promote=false), so this cannot take it silently. Take it deliberately: navig brain takeover --from ${current.owner_id} --reason <why>`,
      { record: current }
    );
  }
  renew(req, current, ownerId, role, ttl, hostname, now) {
    if (current === null) return fail(404, "no_record", "no ownership record exists for this deployment yet \u2014 nothing to renew");
    if (req.epoch !== void 0 && asNumber(req.epoch) !== current.epoch) {
      return fail(409, "stale_epoch", `stale epoch ${asNumber(req.epoch)}: the authority is at epoch ${current.epoch}. This process lost ownership (a takeover happened) and must stop writing.`, { record: current, epoch: current.epoch });
    }
    if (current.released_at !== null) return fail(409, "released", "the ownership record is released \u2014 claim it before renewing", { record: current });
    if (current.owner_id !== ownerId) {
      return fail(409, "not_owner", `this machine (${ownerId}) does not own the Brain \u2014 ${current.hostname || "another machine"} (${current.owner_id}) does`, { record: current });
    }
    const record = { ...current, heartbeat_at: now, updated_at: now, ttl, role, hostname: hostname || current.hostname };
    this.store.saveRecord(record);
    return ok({ record, epoch: record.epoch });
  }
  release(req, current, ownerId, role, hostname, reason, now, requestId) {
    if (current === null) return fail(404, "no_record", "no ownership record exists \u2014 nothing to release");
    if (req.epoch !== void 0 && asNumber(req.epoch) !== current.epoch) {
      return fail(409, "stale_epoch", `stale epoch ${asNumber(req.epoch)} (authority at ${current.epoch}) \u2014 this process no longer owns the record`, { record: current, epoch: current.epoch });
    }
    if (current.owner_id !== ownerId) {
      return fail(409, "not_owner", `refusing to release a record this machine (${ownerId}) does not hold \u2014 it belongs to ${current.owner_id}`, { record: current });
    }
    if (current.released_at !== null) return ok({ record: current, epoch: current.epoch, already_released: true });
    const record = { ...current, released_at: now, updated_at: now, reason: reason || "released", hostname: hostname || current.hostname };
    this.save(record, auditEntry("release", current, record.epoch, ownerId, role, hostname, record.reason, now, requestId, false, false));
    return ok({ record, epoch: record.epoch });
  }
  takeover(req, current, ownerId, role, ttl, hostname, reason, now, requestId, requireExplicitReason = true) {
    if (requireExplicitReason && !reason) {
      return fail(400, "reason_required", "a takeover must state why: pass --reason <why>. It is written to the authority's audit trail and the migration receipt.");
    }
    const fromOwner = asText(req.from_owner);
    if (current === null) {
      const record2 = emptyRecord(now, ownerId, role, ttl, `takeover: ${reason}`, hostname);
      this.save(record2, auditEntry("takeover", null, 1, ownerId, role, hostname, record2.reason, now, requestId, false, false));
      return ok({ record: record2, epoch: record2.epoch });
    }
    if (current.owner_id === ownerId && current.released_at === null) {
      const record2 = { ...current, heartbeat_at: now, updated_at: now, ttl, role, hostname: hostname || current.hostname };
      this.store.saveRecord(record2);
      return ok({ record: record2, epoch: record2.epoch, renewed: true });
    }
    const live = isLive(current, now);
    if (live) {
      if (req.force !== true) {
        return fail(409, "live_requires_force", `${current.hostname || "another machine"} (${current.owner_id}) holds the Brain and is heartbeating (${expiresIn(current, now)}s of TTL left). Stop it first, or pass --force --from ${current.owner_id} if you are deliberately moving the Brain.`, { record: current });
      }
      if (fromOwner !== current.owner_id) {
        return fail(409, "wrong_from", `refusing a forced takeover without matching the current holder: expected --from ${current.owner_id} (got ${fromOwner || "nothing"}). The record may have moved since you looked \u2014 re-run navig brain status and decide again.`, { record: current });
      }
    } else if (fromOwner && fromOwner !== current.owner_id) {
      return fail(409, "wrong_from", `the expired record belongs to ${current.owner_id}, not ${fromOwner}. Re-run navig brain status and decide again.`, { record: current });
    }
    const record = {
      ...current,
      owner_id: ownerId,
      role,
      epoch: current.epoch + 1,
      ttl,
      acquired_at: now,
      heartbeat_at: now,
      updated_at: now,
      hostname,
      reason: reason || current.reason,
      released_at: null,
      takeovers: [...current.takeovers, auditEntry("takeover", current, current.epoch + 1, ownerId, role, hostname, reason, now, requestId, live, !live)].slice(-MAX_TAKEOVERS)
    };
    this.save(record, auditEntry("takeover", current, record.epoch, ownerId, role, hostname, reason, now, requestId, live, !live));
    return ok({ record, epoch: record.epoch });
  }
  markMoved(req, current, ownerId, hostname, reason, now, requestId) {
    if (current === null) return fail(404, "no_record", "no ownership record exists \u2014 nothing to mark as moved");
    if (!reason) return fail(400, "reason_required", "a move must state why: pass --reason <why>");
    const wanted = req.moved_marker || {};
    const to = asText(wanted.to);
    if (!to) return fail(400, "moved_to_required", "moved_marker.to is required \u2014 the authority must record where the Brain went");
    if (req.epoch !== void 0 && asNumber(req.epoch) !== current.epoch) {
      return fail(409, "stale_epoch", `stale epoch ${asNumber(req.epoch)} (authority at ${current.epoch}) \u2014 another machine owns this Brain now; not marking it moved from here`, { record: current, epoch: current.epoch });
    }
    if (current.owner_id !== ownerId) {
      return fail(409, "not_owner", `only the current owner may record the move \u2014 ${current.owner_id} owns the record, not ${ownerId}`, { record: current });
    }
    const marker = {
      to,
      migration: asText(wanted.migration),
      reason,
      at: now,
      from_owner: ownerId
    };
    const record = { ...current, moved_marker: marker, released_at: now, updated_at: now, reason, hostname: hostname || current.hostname };
    this.save(record, auditEntry("mark_moved", current, record.epoch, ownerId, record.role, hostname, reason, now, requestId, false, false));
    return ok({ record, epoch: record.epoch, moved_marker: marker });
  }
  clearMoved(req, current, ownerId, hostname, reason, now, requestId) {
    if (current === null) return fail(404, "no_record", "no ownership record exists \u2014 nothing to clear");
    if (!current.moved_marker) return ok({ record: current, epoch: current.epoch, already_clear: true });
    if (req.epoch !== void 0 && asNumber(req.epoch) !== current.epoch) {
      return fail(409, "stale_epoch", `stale epoch ${asNumber(req.epoch)} (authority at ${current.epoch})`, { record: current, epoch: current.epoch });
    }
    if (current.moved_marker.from_owner !== ownerId) {
      return fail(403, "not_moved_owner", `only ${current.moved_marker.from_owner} \u2014 the machine the record says moved \u2014 may clear its own move marker`, { record: current });
    }
    const record = { ...current, moved_marker: null, updated_at: now, hostname: hostname || current.hostname };
    this.save(record, auditEntry("clear_moved", current, record.epoch, ownerId, record.role, hostname, reason || "move rolled back", now, requestId, false, false));
    return ok({ record, epoch: record.epoch, cleared: true });
  }
  save(record, entry) {
    this.store.saveRecord(record);
    const trail = this.store.loadAudit();
    trail.push(entry);
    this.store.saveAudit(trail);
  }
};

// src/ownership.ts
var MAX_BODY_BYTES = 8 * 1024;
var AUDIT_CAP = 200;
var IDEM_TTL_S = 600;
function json(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" }
  });
}
__name(json, "json");
var SqliteEngineStore = class {
  static {
    __name(this, "SqliteEngineStore");
  }
  sql;
  constructor(state) {
    this.sql = state.storage.sql;
    this.sql.exec(
      `CREATE TABLE IF NOT EXISTS ownership_record (
         id INTEGER PRIMARY KEY CHECK (id = 1),
         json TEXT NOT NULL
       );
       CREATE TABLE IF NOT EXISTS ownership_audit (
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         at REAL NOT NULL,
         json TEXT NOT NULL
       );
       CREATE TABLE IF NOT EXISTS ownership_idem (
         key TEXT PRIMARY KEY,
         at REAL NOT NULL,
         json TEXT NOT NULL
       );`
    );
  }
  loadRecord() {
    const rows = this.sql.exec(`SELECT json FROM ownership_record WHERE id = 1`).toArray();
    if (!rows.length) return null;
    try {
      return recordFromJson(JSON.parse(rows[0].json));
    } catch {
      return null;
    }
  }
  saveRecord(record) {
    this.sql.exec(
      `INSERT INTO ownership_record (id, json) VALUES (1, ?)
       ON CONFLICT(id) DO UPDATE SET json = excluded.json`,
      JSON.stringify(record)
    );
  }
  loadAudit() {
    const rows = this.sql.exec(`SELECT json FROM ownership_audit ORDER BY id ASC`).toArray();
    const out = [];
    for (const row of rows) {
      try {
        const parsed = JSON.parse(row.json);
        if (parsed && typeof parsed === "object") out.push(parsed);
      } catch {
      }
    }
    return out;
  }
  saveAudit(entries) {
    const known = this.loadAudit().length;
    const fresh = entries.slice(known);
    for (const entry of fresh) {
      this.sql.exec(
        `INSERT INTO ownership_audit (at, json) VALUES (?, ?)`,
        Number(entry.at) || 0,
        JSON.stringify(entry)
      );
    }
    this.sql.exec(
      `DELETE FROM ownership_audit WHERE id <= (SELECT MAX(id) - ? FROM ownership_audit)`,
      AUDIT_CAP
    );
  }
  loadIdempotent(key) {
    const rows = this.sql.exec(`SELECT json, at FROM ownership_idem WHERE key = ?`, key).toArray();
    if (!rows.length) return null;
    const age = Date.now() / 1e3 - Number(rows[0].at);
    if (age > IDEM_TTL_S) {
      this.sql.exec(`DELETE FROM ownership_idem WHERE key = ?`, key);
      return null;
    }
    try {
      const parsed = JSON.parse(rows[0].json);
      return parsed && typeof parsed === "object" ? parsed : null;
    } catch {
      return null;
    }
  }
  saveIdempotent(key, body) {
    this.sql.exec(
      `INSERT INTO ownership_idem (key, at, json) VALUES (?, ?, ?)
       ON CONFLICT(key) DO UPDATE SET at = excluded.at, json = excluded.json`,
      key,
      Date.now() / 1e3,
      JSON.stringify(body)
    );
    this.sql.exec(`DELETE FROM ownership_idem WHERE at < ?`, Date.now() / 1e3 - IDEM_TTL_S);
  }
};
var OwnershipRecord = class {
  static {
    __name(this, "OwnershipRecord");
  }
  engine;
  constructor(state, _env) {
    this.engine = new OwnershipEngine(new SqliteEngineStore(state));
  }
  async fetch(request) {
    if (request.method !== "POST") {
      return json(405, { ok: false, code: "method_not_allowed", error: "POST a JSON ownership request" });
    }
    const text = await request.text();
    if (text.length > MAX_BODY_BYTES) {
      return json(413, { ok: false, code: "body_too_large", error: `ownership requests are capped at ${MAX_BODY_BYTES} bytes` });
    }
    let body;
    try {
      const parsed = JSON.parse(text || "{}");
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        return json(400, { ok: false, code: "invalid_json", error: "ownership request must be a JSON object" });
      }
      body = parsed;
    } catch {
      return json(400, { ok: false, code: "invalid_json", error: "ownership request must be valid JSON" });
    }
    const result = this.engine.handle(body);
    return json(result.status, result.body);
  }
};

// src/tenant.ts
var TENANT_RE = /^[0-9a-f]{64}$/;
function isTenantId(segment) {
  return typeof segment === "string" && TENANT_RE.test(segment);
}
__name(isTenantId, "isTenantId");
async function sha256Hex(input) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(input));
  const bytes = new Uint8Array(digest);
  let out = "";
  for (let i = 0; i < bytes.length; i++) out += bytes[i].toString(16).padStart(2, "0");
  return out;
}
__name(sha256Hex, "sha256Hex");

// src/index.ts
var LIGHTHOUSE_VERSION = "1.8.1";
var app = new Hono3();
app.use(
  "*",
  cors({
    origin: /* @__PURE__ */ __name((o) => o || "*", "origin"),
    allowMethods: ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allowHeaders: [
      "Authorization",
      "Content-Type",
      "X-Telegram-Init-Data",
      "X-Telegram-User"
    ],
    maxAge: 86400
  })
);
function bearer(c) {
  const m = (c.req.header("Authorization") || "").match(/^Bearer\s+(.+)$/i);
  return m ? m[1].trim() : null;
}
__name(bearer, "bearer");
function brainFor(env2, hashHex) {
  return env2.BRAIN.get(env2.BRAIN.idFromName(hashHex));
}
__name(brainFor, "brainFor");
function ownershipFor(env2, hashHex) {
  return env2.OWNERSHIP.get(env2.OWNERSHIP.idFromName(hashHex));
}
__name(ownershipFor, "ownershipFor");
function viaTenantPath(c, tenant, internalPath) {
  if (!isTenantId(tenant)) {
    return new Response(JSON.stringify({ error: "not_found" }), {
      status: 404,
      headers: { "content-type": "application/json" }
    });
  }
  const req = internalPath ? new Request(`https://brain${internalPath}`, c.req.raw) : c.req.raw;
  return brainFor(c.env, tenant).fetch(req);
}
__name(viaTenantPath, "viaTenantPath");
async function viaBearer(c, internalPath) {
  const key = bearer(c);
  if (!key) return new Response(JSON.stringify({ error: "unauthorized" }), { status: 401 });
  const hash = await sha256Hex(key);
  const req = internalPath ? new Request(`https://brain${internalPath}`, c.req.raw) : c.req.raw;
  return brainFor(c.env, hash).fetch(req);
}
__name(viaBearer, "viaBearer");
async function viaOwnership(c) {
  const key = bearer(c);
  if (!key) return new Response(JSON.stringify({ error: "unauthorized" }), { status: 401 });
  const hash = await sha256Hex(key);
  const headers = new Headers(c.req.raw.headers);
  headers.delete("content-length");
  let method = c.req.raw.method;
  let body = null;
  if (method === "GET" || method === "HEAD") {
    const owner = new URL(c.req.url).searchParams.get("owner_id") || "";
    method = "POST";
    body = JSON.stringify({ op: "status", owner_id: owner });
    headers.set("content-type", "application/json");
  } else {
    body = c.req.raw.body;
  }
  const req = new Request("https://ownership/__ownership", { method, headers, body });
  return ownershipFor(c.env, hash).fetch(req);
}
__name(viaOwnership, "viaOwnership");
app.get("/uplink", (c) => viaBearer(c, "/__uplink"));
app.get("/api/events", async (c) => {
  const key = c.req.query("key") || bearer(c);
  if (!key) return new Response(JSON.stringify({ error: "unauthorized" }), { status: 401 });
  const hash = await sha256Hex(key);
  return brainFor(c.env, hash).fetch(new Request("https://brain/__events", c.req.raw));
});
app.all("/api/ownership", (c) => viaOwnership(c));
app.all("/api/ownership/*", (c) => viaOwnership(c));
app.all("/api/*", (c) => viaBearer(c));
app.all("/runtime/*", (c) => viaBearer(c));
app.all("/mesh/*", (c) => viaBearer(c));
app.post("/tg/:tenant", (c) => {
  if (!c.req.header("X-Telegram-Bot-Api-Secret-Token")) {
    return c.json({ error: "forbidden" }, 403);
  }
  return viaTenantPath(c, c.req.param("tenant"));
});
app.post("/sms/:tenant", (c) => viaTenantPath(c, c.req.param("tenant")));
app.post("/ingest/:tenant/:source", (c) => viaTenantPath(c, c.req.param("tenant")));
app.get("/oauth/callback", (c) => c.redirect(`http://localhost:8976/oauth/callback${new URL(c.req.url).search}`, 302));
app.get("/os/:tenant/connect", (c) => {
  const tenant = c.req.param("tenant");
  return viaTenantPath(c, tenant, "/__os");
});
app.post("/os/:tenant/pair", (c) => viaTenantPath(c, c.req.param("tenant")));
app.all("/os/:tenant/api/deck/*", (c) => viaTenantPath(c, c.req.param("tenant")));
app.get("/", (c) => c.json({ ok: true, service: "navig-lighthouse", version: LIGHTHOUSE_VERSION }));
app.notFound((c) => c.json({ error: "not_found" }, 404));
var index_default = app;
export {
  BrainSocket,
  OwnershipRecord,
  index_default as default
};
//# sourceMappingURL=index.js.map
