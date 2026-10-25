import { ContractCode, ContractValidationError } from "./errors.js";
import { parseStrictJson } from "./strictJson.js";

export type Direction = "higher_is_better" | "lower_is_better";
export type ClampPolicy = "reject" | "clamp";
export type Operation = "add" | "subtract";
export type MethodId = "weighted_mean" | "additive" | "minimum";

export interface NativeScale {
  readonly minimum: number;
  readonly maximum: number;
  readonly direction: Direction;
}

export interface Interval {
  readonly low: number;
  readonly high: number;
}

export interface Component {
  readonly id: string;
  readonly label: string;
  readonly value: number;
  readonly scale: NativeScale;
  readonly interval: Interval | null;
  readonly weight: number | null;
}

export interface WeightedComponent extends Component {
  readonly weight: number;
}

export interface AdditiveTerm {
  readonly id: string;
  readonly label: string;
  readonly value: number;
  readonly coefficient: number;
  readonly operation: Operation;
  readonly interval: Interval | null;
}

export interface WeightedMeanRequest {
  readonly method: "weighted_mean";
  readonly method_version: string;
  readonly components: ReadonlyArray<WeightedComponent>;
  readonly clamp: ClampPolicy;
}

export interface AdditiveRequest {
  readonly method: "additive";
  readonly method_version: string;
  readonly terms: ReadonlyArray<AdditiveTerm>;
  readonly clamp: ClampPolicy | null;
  readonly intercept: number;
}

export interface MinimumRequest {
  readonly method: "minimum";
  readonly method_version: string;
  readonly components: ReadonlyArray<Component>;
  readonly clamp: ClampPolicy;
}

export type ScoreRequest =
  | WeightedMeanRequest
  | AdditiveRequest
  | MinimumRequest;

export interface Method {
  readonly id: MethodId;
  readonly version: string;
}

export interface ExplainedComponent {
  readonly id: string;
  readonly raw: number;
  readonly normalized: number | null;
  readonly declared_weight: number | null;
  readonly operation: Operation;
  readonly coefficient: number;
  readonly contribution: number;
  readonly contribution_interval: Interval | null;
}

export interface ScoreResult {
  readonly schema: "assay.result/v1";
  readonly method: Method;
  readonly score: number;
  readonly interval: Interval | null;
  readonly clamp: ClampPolicy | null;
  readonly intercept: number | null;
  readonly weight_total: number | null;
  readonly components: ReadonlyArray<ExplainedComponent>;
  readonly inputs_hash: string;
  readonly selected_component_id: string | null;
}

type UnknownRecord = Readonly<Record<string, unknown>>;

const IDENTIFIER = /^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$/u;
const INPUTS_HASH = /^sha256:[0-9a-f]{64}$/u;
const METHODS = ["weighted_mean", "additive", "minimum"] as const;
const DIRECTIONS = ["higher_is_better", "lower_is_better"] as const;
const CLAMP_POLICIES = ["reject", "clamp"] as const;
const OPERATIONS = ["add", "subtract"] as const;

function fail(code: ContractCode): never {
  throw new ContractValidationError(code);
}

function invalidObject(): never {
  fail(ContractCode.INVALID_OBJECT);
}

interface ReflectedShape {
  readonly keys: ReadonlyArray<PropertyKey>;
  readonly prototype: object | null;
}

function reflectedShape(value: object): ReflectedShape {
  return {
    keys: Reflect.ownKeys(value),
    prototype: Reflect.getPrototypeOf(value),
  };
}

function sameKeys(
  first: ReadonlyArray<PropertyKey>,
  second: ReadonlyArray<PropertyKey>,
): boolean {
  return (
    first.length === second.length &&
    first.every((key, index) => key === second[index])
  );
}

function requireStableShape(value: object, shape: ReflectedShape): void {
  const current = reflectedShape(value);
  if (
    current.prototype !== shape.prototype ||
    !sameKeys(current.keys, shape.keys)
  ) {
    invalidObject();
  }
}

function dataValue(
  source: object,
  key: PropertyKey,
  enumerable = true,
): unknown {
  const descriptor = Reflect.getOwnPropertyDescriptor(source, key);
  if (
    descriptor === undefined ||
    descriptor.enumerable !== enumerable ||
    !("value" in descriptor)
  ) {
    invalidObject();
  }
  return descriptor.value;
}

function snapshotRecord(source: object, seen: WeakSet<object>): UnknownRecord {
  const shape = reflectedShape(source);
  if (shape.prototype !== Object.prototype && shape.prototype !== null) {
    invalidObject();
  }
  const output: Record<string, unknown> = Object.create(null) as Record<
    string,
    unknown
  >;
  for (const key of shape.keys) {
    if (typeof key !== "string") invalidObject();
    output[key] = snapshotValue(dataValue(source, key), seen);
  }
  requireStableShape(source, shape);
  return output;
}

function arrayLength(source: object, shape: ReflectedShape): number {
  const value = dataValue(source, "length", false);
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    invalidObject();
  }
  if (shape.keys.length !== value + 1) invalidObject();
  return value;
}

function snapshotArray(
  source: ReadonlyArray<unknown>,
  seen: WeakSet<object>,
): ReadonlyArray<unknown> {
  const shape = reflectedShape(source);
  if (shape.prototype !== Array.prototype) invalidObject();
  const length = arrayLength(source, shape);
  const keys = new Set(shape.keys);
  const output: unknown[] = [];
  for (let index = 0; index < length; index += 1) {
    const key = String(index);
    if (!keys.has(key)) invalidObject();
    output.push(snapshotValue(dataValue(source, key), seen));
  }
  requireStableShape(source, shape);
  return output;
}

function snapshotValue(value: unknown, seen: WeakSet<object>): unknown {
  if (value === null || typeof value !== "object") return value;
  if (seen.has(value)) invalidObject();
  seen.add(value);
  try {
    return Array.isArray(value)
      ? snapshotArray(value, seen)
      : snapshotRecord(value, seen);
  } finally {
    seen.delete(value);
  }
}

function snapshotBoundary(value: unknown): unknown {
  try {
    return snapshotValue(value, new WeakSet<object>());
  } catch {
    return invalidObject();
  }
}

function isPlainRecord(value: unknown): value is UnknownRecord {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const prototype: unknown = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function record(value: unknown): UnknownRecord {
  if (!isPlainRecord(value)) {
    fail(ContractCode.INVALID_OBJECT);
  }
  return value;
}

function hasOwn(value: UnknownRecord, key: string): boolean {
  return Object.hasOwn(value, key);
}

function shape(
  value: unknown,
  allowed: ReadonlyArray<string>,
  required: ReadonlyArray<string>,
): UnknownRecord {
  const candidate = record(value);
  if (Object.keys(candidate).some((key) => !allowed.includes(key))) {
    fail(ContractCode.UNKNOWN_FIELD);
  }
  if (required.some((key) => !hasOwn(candidate, key))) {
    fail(ContractCode.MISSING_FIELD);
  }
  return candidate;
}

function canonicalZero(value: number): number {
  return value === 0 ? 0 : value;
}

function finite(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    fail(ContractCode.INVALID_NUMBER);
  }
  return canonicalZero(value);
}

function positive(value: unknown): number {
  const parsed = finite(value);
  if (parsed <= 0) {
    fail(ContractCode.INVALID_WEIGHT);
  }
  return parsed;
}

function nonnegative(value: unknown): number {
  const parsed = finite(value);
  if (parsed < 0) {
    fail(ContractCode.INVALID_COEFFICIENT);
  }
  return parsed;
}

function hasInvalidSurrogate(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!Number.isFinite(next) || next < 0xdc00 || next > 0xdfff) return true;
      index += 1;
    } else if (code >= 0xdc00 && code <= 0xdfff) {
      return true;
    }
  }
  return false;
}

function identifier(value: unknown): string {
  if (typeof value !== "string" || value.length > 128) {
    fail(ContractCode.INVALID_IDENTIFIER);
  }
  if (!IDENTIFIER.test(value)) {
    fail(ContractCode.INVALID_IDENTIFIER);
  }
  return value;
}

function label(value: unknown): string {
  if (typeof value !== "string" || !value.trim()) {
    fail(ContractCode.INVALID_LABEL);
  }
  if (hasInvalidSurrogate(value)) {
    fail(ContractCode.INVALID_TEXT);
  }
  if (Array.from(value).length > 256) {
    fail(ContractCode.INVALID_LABEL);
  }
  return value;
}

function member<const T extends string>(
  value: unknown,
  values: ReadonlyArray<T>,
  code: ContractCode,
): T {
  if (typeof value !== "string" || !values.includes(value as T)) {
    fail(code);
  }
  return value as T;
}

function direction(value: unknown): Direction {
  return member(value, DIRECTIONS, ContractCode.INVALID_DIRECTION);
}

function clamp(value: unknown): ClampPolicy {
  return member(value, CLAMP_POLICIES, ContractCode.INVALID_CLAMP_POLICY);
}

function operation(value: unknown): Operation {
  return member(value, OPERATIONS, ContractCode.INVALID_OPERATION);
}

function methodId(value: unknown): MethodId {
  return member(value, METHODS, ContractCode.INVALID_METHOD);
}

function optionalNumber(value: unknown): number | null {
  return value === null ? null : finite(value);
}

function optionalPositive(value: unknown): number | null {
  return value === null ? null : positive(value);
}

function optionalClamp(value: unknown): ClampPolicy | null {
  return value === null ? null : clamp(value);
}

function parseScale(value: unknown): NativeScale {
  const source = shape(
    value,
    ["minimum", "maximum", "direction"],
    ["minimum", "maximum", "direction"],
  );
  const parsed = {
    minimum: finite(source.minimum),
    maximum: finite(source.maximum),
    direction: direction(source.direction),
  } satisfies NativeScale;
  if (parsed.maximum <= parsed.minimum) fail(ContractCode.INVALID_SCALE);
  return parsed;
}

export function parseNativeScale(value: unknown): NativeScale {
  return deepFreeze(parseScale(snapshotBoundary(value)));
}

export function parseClampPolicy(value: unknown): ClampPolicy {
  return clamp(value);
}

export function finiteNumber(value: unknown): number {
  return finite(value);
}

function parseInterval(value: unknown): Interval {
  const source = shape(value, ["low", "high"], ["low", "high"]);
  const parsed = { low: finite(source.low), high: finite(source.high) };
  if (parsed.high <= parsed.low) fail(ContractCode.INVALID_INTERVAL);
  return parsed;
}

function optionalInterval(value: unknown): Interval | null {
  return value === null ? null : parseInterval(value);
}

function requirePointInInterval(
  value: number,
  interval: Interval | null,
): void {
  if (interval !== null && (value < interval.low || value > interval.high)) {
    fail(ContractCode.INVALID_INTERVAL);
  }
}

function parseComponent(value: unknown): Component {
  const source = shape(
    value,
    ["id", "label", "value", "scale", "interval", "weight"],
    ["id", "label", "value", "scale"],
  );
  const parsed = {
    id: identifier(source.id),
    label: label(source.label),
    value: finite(source.value),
    scale: parseScale(source.scale),
    interval: optionalInterval(source.interval ?? null),
    weight: optionalPositive(source.weight ?? null),
  } satisfies Component;
  requirePointInInterval(parsed.value, parsed.interval);
  return parsed;
}

function parseTerm(value: unknown): AdditiveTerm {
  const source = shape(
    value,
    ["id", "label", "value", "coefficient", "operation", "interval"],
    ["id", "label", "value", "coefficient", "operation"],
  );
  const parsed = {
    id: identifier(source.id),
    label: label(source.label),
    value: finite(source.value),
    coefficient: nonnegative(source.coefficient),
    operation: operation(source.operation),
    interval: optionalInterval(source.interval ?? null),
  } satisfies AdditiveTerm;
  requirePointInInterval(parsed.value, parsed.interval);
  return parsed;
}

function array(value: unknown): ReadonlyArray<unknown> {
  if (!Array.isArray(value)) fail(ContractCode.INVALID_CONTRACT);
  return value;
}

function uniqueIds(items: ReadonlyArray<{ readonly id: string }>): void {
  const ids = new Set(items.map((item) => item.id));
  if (ids.size !== items.length) fail(ContractCode.DUPLICATE_IDENTIFIER);
}

function componentInRange(component: Component): boolean {
  const values = [component.value];
  if (component.interval !== null) {
    values.push(component.interval.low, component.interval.high);
  }
  return values.every(
    (value) =>
      value >= component.scale.minimum && value <= component.scale.maximum,
  );
}

function validateComponents(
  components: ReadonlyArray<Component>,
  policy: ClampPolicy,
): void {
  if (components.length === 0) fail(ContractCode.EMPTY_COMPONENTS);
  uniqueIds(components);
  if (policy === "reject" && !components.every(componentInRange)) {
    fail(ContractCode.OUT_OF_RANGE);
  }
}

function parseWeighted(source: UnknownRecord): WeightedMeanRequest {
  const shaped = shape(
    source,
    ["method", "method_version", "components", "clamp"],
    ["method", "method_version", "components", "clamp"],
  );
  const components = array(shaped.components).map(parseComponent);
  const policy = clamp(shaped.clamp);
  validateComponents(components, policy);
  if (components.some((component) => component.weight === null)) {
    fail(ContractCode.MISSING_WEIGHT);
  }
  return {
    method: "weighted_mean",
    method_version: identifier(shaped.method_version),
    components: components as ReadonlyArray<WeightedComponent>,
    clamp: policy,
  };
}

function parseAdditive(source: UnknownRecord): AdditiveRequest {
  const shaped = shape(
    source,
    ["method", "method_version", "terms", "clamp", "intercept"],
    ["method", "method_version", "terms", "clamp"],
  );
  const terms = array(shaped.terms).map(parseTerm);
  if (terms.length === 0) fail(ContractCode.EMPTY_TERMS);
  uniqueIds(terms);
  return {
    method: "additive",
    method_version: identifier(shaped.method_version),
    terms,
    clamp: optionalClamp(shaped.clamp),
    intercept: finite(shaped.intercept ?? 0),
  };
}

function parseMinimum(source: UnknownRecord): MinimumRequest {
  const shaped = shape(
    source,
    ["method", "method_version", "components", "clamp"],
    ["method", "method_version", "components", "clamp"],
  );
  const components = array(shaped.components).map(parseComponent);
  const policy = clamp(shaped.clamp);
  validateComponents(components, policy);
  return {
    method: "minimum",
    method_version: identifier(shaped.method_version),
    components,
    clamp: policy,
  };
}

function deepFreeze<T extends object>(value: T): T {
  for (const child of Object.values(value)) {
    if (child !== null && typeof child === "object") deepFreeze(child);
  }
  return Object.freeze(value);
}

function decodeJson(input: string): unknown {
  if (typeof input !== "string") fail(ContractCode.INVALID_CONTRACT);
  try {
    return parseStrictJson(input);
  } catch {
    fail(ContractCode.INVALID_CONTRACT);
  }
}

export function parseRequest(input: unknown): ScoreRequest {
  const snapshot = snapshotBoundary(input);
  if (!isPlainRecord(snapshot)) fail(ContractCode.INVALID_METHOD);
  const method = methodId(snapshot.method);
  const parsed =
    method === "weighted_mean"
      ? parseWeighted(snapshot)
      : method === "additive"
        ? parseAdditive(snapshot)
        : parseMinimum(snapshot);
  return deepFreeze(parsed);
}

export function parseRequestJson(input: string): ScoreRequest {
  return parseRequest(decodeJson(input));
}

function parseMethod(value: unknown): Method {
  const source = shape(value, ["id", "version"], ["id", "version"]);
  return { id: methodId(source.id), version: identifier(source.version) };
}

function parseExplained(value: unknown): ExplainedComponent {
  const keys = [
    "id",
    "raw",
    "normalized",
    "declared_weight",
    "operation",
    "coefficient",
    "contribution",
    "contribution_interval",
  ];
  const source = shape(value, keys, keys);
  return {
    id: identifier(source.id),
    raw: finite(source.raw),
    normalized: optionalNumber(source.normalized),
    declared_weight: optionalPositive(source.declared_weight),
    operation: operation(source.operation),
    coefficient: nonnegative(source.coefficient),
    contribution: finite(source.contribution),
    contribution_interval: optionalInterval(source.contribution_interval),
  };
}

function parseHash(value: unknown): string {
  if (typeof value !== "string" || !INPUTS_HASH.test(value)) {
    fail(ContractCode.INVALID_INPUTS_HASH);
  }
  return value;
}

function parseSelected(value: unknown): string | null {
  return value === null ? null : identifier(value);
}

function resultShape(input: unknown): UnknownRecord {
  const keys = [
    "schema",
    "method",
    "score",
    "interval",
    "clamp",
    "intercept",
    "weight_total",
    "components",
    "inputs_hash",
    "selected_component_id",
  ];
  return shape(
    input,
    keys,
    keys.filter((key) => key !== "schema" && key !== "selected_component_id"),
  );
}

function parseSchema(value: unknown): "assay.result/v1" {
  if (value !== "assay.result/v1") fail(ContractCode.INVALID_CONTRACT);
  return value;
}

function buildResult(source: UnknownRecord): ScoreResult {
  const components = array(source.components).map(parseExplained);
  if (components.length === 0) fail(ContractCode.EMPTY_COMPONENTS);
  uniqueIds(components);
  return {
    schema: parseSchema(source.schema ?? "assay.result/v1"),
    method: parseMethod(source.method),
    score: finite(source.score),
    interval: optionalInterval(source.interval),
    clamp: optionalClamp(source.clamp),
    intercept: optionalNumber(source.intercept),
    weight_total: optionalPositive(source.weight_total),
    components,
    inputs_hash: parseHash(source.inputs_hash),
    selected_component_id: parseSelected(source.selected_component_id ?? null),
  };
}

function resultNumber(value: number): number {
  if (!Number.isFinite(value)) fail(ContractCode.INVALID_RESULT);
  return canonicalZero(value);
}

function leftAdd(values: ReadonlyArray<number>, initial = 0): number {
  let total = resultNumber(initial);
  for (const value of values) total = resultNumber(total + value);
  return total;
}

type Bounds = readonly [number, number] | null;

function rowBounds(row: ExplainedComponent): readonly [number, number] {
  const interval = row.contribution_interval;
  return interval === null
    ? [row.contribution, row.contribution]
    : [interval.low, interval.high];
}

function hasIntervals(rows: ReadonlyArray<ExplainedComponent>): boolean {
  return rows.some((row) => row.contribution_interval !== null);
}

function summedBounds(rows: ReadonlyArray<ExplainedComponent>): Bounds {
  if (!hasIntervals(rows)) return null;
  const bounds = rows.map(rowBounds);
  return [
    leftAdd(bounds.map(([low]) => low)),
    leftAdd(bounds.map(([, high]) => high)),
  ];
}

function intervalMatches(actual: Interval | null, expected: Bounds): boolean {
  if (expected === null || expected[0] === expected[1]) return actual === null;
  return actual?.low === expected[0] && actual.high === expected[1];
}

function boundedInterval(row: ExplainedComponent, maximum: number): boolean {
  const interval = row.contribution_interval;
  return (
    interval === null ||
    (interval.low >= 0 &&
      interval.low <= row.contribution &&
      row.contribution <= interval.high &&
      interval.low < interval.high &&
      interval.high <= maximum)
  );
}

function containsContribution(row: ExplainedComponent): boolean {
  const interval = row.contribution_interval;
  return (
    interval === null ||
    (interval.low <= row.contribution && row.contribution <= interval.high)
  );
}

function validWeightedRow(row: ExplainedComponent, total: number): boolean {
  if (row.normalized === null || row.declared_weight === null) return false;
  const coefficient = resultNumber(row.declared_weight / total);
  const contribution = resultNumber(row.normalized * row.coefficient);
  return (
    row.operation === "add" &&
    row.normalized >= 0 &&
    row.normalized <= 1 &&
    row.coefficient === coefficient &&
    row.contribution === contribution &&
    boundedInterval(row, coefficient)
  );
}

function validateWeighted(result: ScoreResult): void {
  if (
    result.selected_component_id !== null ||
    result.intercept !== null ||
    result.clamp === null ||
    result.weight_total === null
  ) {
    fail(ContractCode.INVALID_RESULT);
  }
  const weights = result.components.map(
    (row) => row.declared_weight ?? Number.NaN,
  );
  const total = leftAdd(weights);
  if (result.weight_total !== total) fail(ContractCode.INVALID_RESULT);
  if (!result.components.every((row) => validWeightedRow(row, total))) {
    fail(ContractCode.INVALID_RESULT);
  }
  if (
    result.score !== leftAdd(result.components.map((row) => row.contribution))
  ) {
    fail(ContractCode.INVALID_RESULT);
  }
  if (!intervalMatches(result.interval, summedBounds(result.components))) {
    fail(ContractCode.INVALID_RESULT);
  }
}

function signedAdd(
  total: number,
  row: ExplainedComponent,
  value: number,
): number {
  return resultNumber(row.operation === "add" ? total + value : total - value);
}

function finalResult(value: number, policy: ClampPolicy | null): number {
  const result = resultNumber(value);
  if (policy === null) return result;
  if (policy === "clamp")
    return canonicalZero(Math.min(1, Math.max(0, result)));
  if (result < 0 || result > 1) fail(ContractCode.INVALID_RESULT);
  return result;
}

function additivePoint(result: ScoreResult): number {
  if (result.intercept === null) fail(ContractCode.INVALID_RESULT);
  let total = result.intercept;
  for (const row of result.components)
    total = signedAdd(total, row, row.contribution);
  return finalResult(total, result.clamp);
}

function advanceBounds(
  low: number,
  high: number,
  row: ExplainedComponent,
): readonly [number, number] {
  const [rowLow, rowHigh] = rowBounds(row);
  return row.operation === "add"
    ? [signedAdd(low, row, rowLow), signedAdd(high, row, rowHigh)]
    : [signedAdd(low, row, rowHigh), signedAdd(high, row, rowLow)];
}

function additiveBounds(result: ScoreResult): Bounds {
  if (!hasIntervals(result.components)) return null;
  if (result.intercept === null) fail(ContractCode.INVALID_RESULT);
  let low = result.intercept;
  let high = result.intercept;
  for (const row of result.components)
    [low, high] = advanceBounds(low, high, row);
  return [finalResult(low, result.clamp), finalResult(high, result.clamp)];
}

function validAdditiveRow(row: ExplainedComponent): boolean {
  return (
    row.normalized === null &&
    row.declared_weight === null &&
    row.contribution === resultNumber(row.raw * row.coefficient) &&
    containsContribution(row)
  );
}

function validateAdditive(result: ScoreResult): void {
  if (
    result.selected_component_id !== null ||
    result.intercept === null ||
    result.weight_total !== null ||
    !result.components.every(validAdditiveRow)
  ) {
    fail(ContractCode.INVALID_RESULT);
  }
  if (result.score !== additivePoint(result)) fail(ContractCode.INVALID_RESULT);
  if (!intervalMatches(result.interval, additiveBounds(result))) {
    fail(ContractCode.INVALID_RESULT);
  }
}

function validMinimumRow(row: ExplainedComponent): boolean {
  return (
    row.normalized !== null &&
    row.declared_weight === null &&
    row.operation === "add" &&
    row.coefficient === 1 &&
    row.normalized >= 0 &&
    row.normalized <= 1 &&
    row.contribution === row.normalized &&
    boundedInterval(row, 1)
  );
}

function minimumBounds(rows: ReadonlyArray<ExplainedComponent>): Bounds {
  if (!hasIntervals(rows)) return null;
  let low = Number.POSITIVE_INFINITY;
  let high = Number.POSITIVE_INFINITY;
  for (const row of rows) {
    const [rowLow, rowHigh] = rowBounds(row);
    low = Math.min(low, rowLow);
    high = Math.min(high, rowHigh);
  }
  return [low, high];
}

function firstMinimum(
  rows: ReadonlyArray<ExplainedComponent>,
): ExplainedComponent {
  const first = rows[0];
  if (first === undefined) fail(ContractCode.INVALID_RESULT);
  let selected = first;
  for (const row of rows.slice(1)) {
    if (row.contribution < selected.contribution) selected = row;
  }
  return selected;
}

function validateMinimum(result: ScoreResult): void {
  if (
    result.clamp === null ||
    result.intercept !== null ||
    result.weight_total !== null ||
    !result.components.every(validMinimumRow)
  ) {
    fail(ContractCode.INVALID_RESULT);
  }
  const selected = firstMinimum(result.components);
  if (
    result.selected_component_id !== selected.id ||
    result.score !== selected.contribution
  ) {
    fail(ContractCode.INVALID_RESULT);
  }
  if (!intervalMatches(result.interval, minimumBounds(result.components))) {
    fail(ContractCode.INVALID_RESULT);
  }
}

function validateResult(result: ScoreResult): void {
  if (result.method.id === "weighted_mean") validateWeighted(result);
  else if (result.method.id === "additive") validateAdditive(result);
  else validateMinimum(result);
}

export function parseScoreResult(input: unknown): ScoreResult {
  const result = buildResult(resultShape(snapshotBoundary(input)));
  validateResult(result);
  return deepFreeze(result);
}

export function parseScoreResultJson(input: string): ScoreResult {
  return parseScoreResult(decodeJson(input));
}
