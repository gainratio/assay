import { describe, expect, it } from "vitest";
import { parseRequestJson } from "./contracts.js";
import { parseStrictJson } from "./strictJson.js";

describe("parseStrictJson", () => {
  it("parses ordinary JSON with the same values as JSON.parse", () => {
    const text = '{"a":[1,true,null,"x\\n"],"b":-2.5e+3}';
    expect(parseStrictJson(text)).toEqual(JSON.parse(text));
  });

  it("rejects a duplicate member in one object", () => {
    expect(() => parseStrictJson('{"a":1,"a":2}')).toThrow(SyntaxError);
  });

  it("allows the same key in sibling objects", () => {
    expect(parseStrictJson('[{"id":1},{"id":2}]')).toEqual([
      { id: 1 },
      { id: 2 },
    ]);
  });

  it("treats __proto__ as data, not a prototype setter", () => {
    const value = parseStrictJson('{"__proto__":{"polluted":true}}') as Record<
      string,
      unknown
    >;
    expect(Object.getPrototypeOf(value)).toBe(Object.prototype);
    expect(Object.getOwnPropertyDescriptor(value, "__proto__")?.value).toEqual({
      polluted: true,
    });
  });

  it("is wired into parseRequestJson", () => {
    expect(() => parseRequestJson('{"method":{},"method":{}}')).toThrow();
  });
});
