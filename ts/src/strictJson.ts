/**
 * JSON.parse with duplicate-key rejection.
 *
 * `JSON.parse` keeps the last occurrence of a repeated key, silently dropping
 * the first. The Python contract rejects duplicate members, so a request could
 * validate in Python and mean something different in TypeScript. This parser
 * walks the same grammar and refuses a repeated key in any object.
 *
 * It is deliberately small and dependency-free. Values are plain JS values;
 * strings are decoded by `JSON.parse` on the already-validated token. Property
 * writes use `defineProperty` so a `__proto__` key becomes data, not a setter.
 */

const NUMBER = /-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/y;
const MAX_DEPTH = 128;

class StrictJsonParser {
  private readonly input: string;
  private index = 0;
  private depth = 0;

  public constructor(input: string) {
    this.input = input;
  }

  public parse(): unknown {
    this.skipWhitespace();
    const value = this.value();
    this.skipWhitespace();
    if (this.index !== this.input.length) this.fail();
    return value;
  }

  private fail(): never {
    throw new SyntaxError("invalid JSON");
  }

  private skipWhitespace(): void {
    while (this.index < this.input.length) {
      const code = this.input.charCodeAt(this.index);
      if (code !== 0x20 && code !== 0x09 && code !== 0x0a && code !== 0x0d)
        break;
      this.index += 1;
    }
  }

  private value(): unknown {
    this.skipWhitespace();
    const char = this.input[this.index];
    if (char === "{") return this.object();
    if (char === "[") return this.array();
    if (char === '"') return this.string();
    if (char === "t" && this.input.startsWith("true", this.index)) {
      this.index += 4;
      return true;
    }
    if (char === "f" && this.input.startsWith("false", this.index)) {
      this.index += 5;
      return false;
    }
    if (char === "n" && this.input.startsWith("null", this.index)) {
      this.index += 4;
      return null;
    }
    return this.number();
  }

  private number(): number {
    NUMBER.lastIndex = this.index;
    const match = NUMBER.exec(this.input);
    if (match === null) this.fail();
    this.index = NUMBER.lastIndex;
    return Number(match[0]);
  }

  private string(): string {
    const start = this.index;
    this.index += 1;
    while (this.index < this.input.length) {
      const code = this.input.charCodeAt(this.index);
      if (code === 0x22) {
        this.index += 1;
        return JSON.parse(this.input.slice(start, this.index)) as string;
      }
      if (code === 0x5c) {
        this.index += 2;
        continue;
      }
      if (code < 0x20) this.fail();
      this.index += 1;
    }
    return this.fail();
  }

  private array(): unknown[] {
    this.enter();
    this.index += 1;
    this.skipWhitespace();
    const out: unknown[] = [];
    if (this.input[this.index] === "]") {
      this.index += 1;
      this.depth -= 1;
      return out;
    }
    while (true) {
      out.push(this.value());
      this.skipWhitespace();
      const char = this.input[this.index];
      if (char === "]") {
        this.index += 1;
        this.depth -= 1;
        return out;
      }
      if (char !== ",") this.fail();
      this.index += 1;
    }
  }

  private object(): Record<string, unknown> {
    this.enter();
    this.index += 1;
    this.skipWhitespace();
    const out: Record<string, unknown> = {};
    const seen = new Set<string>();
    if (this.input[this.index] === "}") {
      this.index += 1;
      this.depth -= 1;
      return out;
    }
    while (true) {
      this.skipWhitespace();
      if (this.input[this.index] !== '"') this.fail();
      const key = this.string();
      if (seen.has(key)) this.fail();
      seen.add(key);
      this.skipWhitespace();
      if (this.input[this.index] !== ":") this.fail();
      this.index += 1;
      const value = this.value();
      Object.defineProperty(out, key, {
        value,
        enumerable: true,
        configurable: true,
        writable: true,
      });
      this.skipWhitespace();
      const char = this.input[this.index];
      if (char === "}") {
        this.index += 1;
        this.depth -= 1;
        return out;
      }
      if (char !== ",") this.fail();
      this.index += 1;
    }
  }

  private enter(): void {
    this.depth += 1;
    if (this.depth > MAX_DEPTH) this.fail();
  }
}

/** Parse JSON, rejecting any object with a repeated key. */
export function parseStrictJson(input: string): unknown {
  return new StrictJsonParser(input).parse();
}
